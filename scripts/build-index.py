#!/usr/bin/env python3
"""build-index.py — Build the LA3D-LLM-Agents federation index.

Two discovery paths, both fed to the same Card-projection pipeline:

  1. ORG WALK: every non-archived, non-infrastructure repo in the
     LA3D-LLM-Agents org. Trusted by org membership.

  2. TOPIC WALK: every repo tagged with TOPIC_NAME, FILTERED by an
     allowlist of trusted owners (TRUSTED_TOPIC_OWNERS). Lets agents
     outside the org participate without forking, while keeping the
     trust gate narrow.

Org entries win on duplication. Each index entry carries a
`provenance: "org" | "topic"` field so callers can distinguish.

Honest sparsity: only fields the Card actually declares are projected;
the rest are derived (clone_url, home_url, card_url). Cards under the
new convention nest llm-wiki-specific fields under x-llm-wiki:;
top-level fallbacks are also accepted for legacy Cards.

Skipped without failing the run:
- Repos with no accessible wiki (404, auth, archive)
- Wikis with no Card_<repo>.md
- Cards whose frontmatter cannot be parsed or validated (with per-card diagnostics)
- Internal infrastructure repos (.github, the Pages repo itself)
- Topic-tagged repos whose owner is NOT in the allowlist

The script is intended to be run from the repo root of
la3d-llm-agents.github.io; it writes ./index.json.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ORG = "LA3D-LLM-Agents"
INDEX_PATH = Path("index.json")
INDEX_SCHEMA_VERSION = "0.3.0"
CARD_SCHEMA = json.loads(
    (
        Path(__file__).resolve().parent.parent / "schemas/agent-card-1.0.schema.json"
    ).read_text(encoding="utf-8")
)
CARD_VALIDATOR = Draft202012Validator(CARD_SCHEMA)

EXCLUDE_REPOS = {".github", "la3d-llm-agents.github.io"}

# Discovery topic. Pre-existing convention; honored by the topic-walk
# secondary discovery path. To opt in to the federation from a repo
# outside the org, add this topic and ensure your wiki has a
# Card_<repo>.md.
TOPIC_NAME = "nd-llm-wiki"

# Allowlist for topic-walk discovery. Owners not in this set are
# silently skipped — prevents topic squatting from putting strangers
# in the federation index. Add new collaborators here by PR.
# Case-insensitive comparison (GitHub usernames are case-insensitive).
TRUSTED_TOPIC_OWNERS = {
    "LA3D-LLM-Agents",  # ourselves; redundant since org-walk covers, kept for clarity
    "LA3D",  # the broader LA3D org
    "crcresearch",  # the template owner
    "PaperAnalyticalDeviceND",  # Priscila + Maximilian's PAD/chemopad domain
    "chrissweet",  # personal
    "charlesvardeman",  # LA3D co-owner
    "psaboia",  # Priscila Saboia Moreira
}
TRUSTED_TOPIC_OWNERS_LC = {o.lower() for o in TRUSTED_TOPIC_OWNERS}


class CardError(ValueError):
    """An invalid card that can be diagnosed independently of other cards."""


class CardLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently selecting one value."""


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in result:
                raise CardError(f"duplicate YAML key {key!r}")
            result[key] = loader.construct_object(value_node, deep=deep)
        except TypeError as exc:
            raise CardError("YAML mapping keys must be scalar") from exc
    return result


CardLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def string_list(value, path):
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise CardError(f"{path}: expected a list of strings")


def validate_card(card):
    if not isinstance(card, dict):
        raise CardError("frontmatter: expected an object")
    for key in ("id", "description"):
        if key in card and not isinstance(card[key], str):
            raise CardError(f"{key}: expected a string")
    if card.get("id") and not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", card["id"]
    ):
        raise CardError("id: expected owner/repository identity")
    for key in ("topics", "capabilities"):
        if key in card:
            string_list(card[key], key)
    x = card.get("x-llm-wiki", {})
    if not isinstance(x, dict):
        raise CardError("x-llm-wiki: expected an object")
    if "topics" in x:
        string_list(x["topics"], "x-llm-wiki.topics")
    if "endpoints" in x and not isinstance(x["endpoints"], dict):
        raise CardError("x-llm-wiki.endpoints: expected an object")
    if "x-fabric-card" not in card:
        return
    structured = card["x-fabric-card"]
    error = next(CARD_VALIDATOR.iter_errors(structured), None)
    if error is not None:
        path = ".".join(str(p) for p in error.absolute_path)
        raise CardError(f"x-fabric-card{'.' + path if path else ''}: {error.message}")
    for key in ("skills", "knowledge_bundles", "interfaces"):
        seen = set()
        for item in structured[key]:
            if item["id"] in seen:
                raise CardError(f"x-fabric-card.{key}: duplicate id {item['id']!r}")
            seen.add(item["id"])
    bundles = {b["id"] for b in structured["knowledge_bundles"]}
    for interface in structured["interfaces"]:
        if (
            interface["kind"] == "clone-and-invoke"
            and interface["bundle_id"] not in bundles
        ):
            raise CardError(
                f"x-fabric-card.interfaces.{interface['id']}: unknown bundle_id {interface['bundle_id']!r}"
            )
    compatibility = {
        "id": structured["id"],
        "description": structured["description"],
        "capabilities": [skill["description"] for skill in structured["skills"]],
    }
    for key, value in compatibility.items():
        if key in card and card[key] != value:
            raise CardError(
                f"{key}: conflicts with x-fabric-card compatibility projection"
            )


def gh_token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def list_org_repos() -> list[dict]:
    """Return non-archived non-infrastructure repos in the org."""
    out = subprocess.check_output(
        ["gh", "api", "--paginate", f"/orgs/{ORG}/repos?per_page=100"],
        text=True,
    )
    repos = json.loads(out)
    return [
        {"name": r["name"], "owner": r["owner"]["login"], "private": r["private"]}
        for r in repos
        if r["name"] not in EXCLUDE_REPOS and not r["archived"]
    ]


def list_topic_repos() -> list[dict]:
    """Return repos tagged with TOPIC_NAME, filtered to allowlisted owners.

    Repos whose owner is not in TRUSTED_TOPIC_OWNERS are silently skipped
    (with a log line). Archived repos and the infrastructure-exclude set
    are also skipped. Soft-fails (returns empty list) if `gh search`
    errors — topic-walk is a secondary path; org-walk is always tried.
    """
    try:
        out = subprocess.check_output(
            [
                "gh",
                "search",
                "repos",
                "--topic",
                TOPIC_NAME,
                "--limit",
                "100",
                "--json",
                "fullName,owner,isPrivate,isArchived",
            ],
            text=True,
        )
        repos = json.loads(out)
    except subprocess.CalledProcessError as e:
        print(f"  warning: gh search topic {TOPIC_NAME} failed: {e}", file=sys.stderr)
        return []

    out_repos: list[dict] = []
    for r in repos:
        if r.get("isArchived"):
            continue
        full = r["fullName"]
        owner = r["owner"]["login"]
        if owner.lower() not in TRUSTED_TOPIC_OWNERS_LC:
            print(
                f"  topic-walk: skipping {full} (owner '{owner}' not in trusted allowlist)",
                file=sys.stderr,
            )
            continue
        name = full.split("/", 1)[1]
        if name in EXCLUDE_REPOS:
            continue
        out_repos.append(
            {
                "name": name,
                "owner": owner,
                "private": bool(r.get("isPrivate", False)),
            }
        )
    return out_repos


def clone_wiki(repo: dict, dest: Path) -> bool:
    """Clone <owner>/<repo>.wiki.git into dest. True on success."""
    owner, name = repo["owner"], repo["name"]
    token = gh_token()
    if token:
        url = f"https://x-access-token:{token}@github.com/{owner}/{name}.wiki.git"
    else:
        url = f"https://github.com/{owner}/{name}.wiki.git"
    try:
        subprocess.run(
            ["git", "clone", "--depth=1", "--quiet", url, str(dest)],
            check=True,
            capture_output=True,
            timeout=60,
        )
        return True
    except subprocess.CalledProcessError as e:
        msg = e.stderr.decode(errors="replace").strip().splitlines()[-1:]
        print(f"  {owner}/{name}: wiki clone failed ({msg}); skipping", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print(f"  {owner}/{name}: wiki clone timed out; skipping", file=sys.stderr)
        return False


def parse_card(card_path: Path) -> dict:
    """Parse the frontmatter block of a Card_<repo>.md file."""
    text = card_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise CardError("frontmatter: missing opening --- delimiter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise CardError("frontmatter: missing closing --- delimiter")
    try:
        card = yaml.load(text[4:end], Loader=CardLoader)
    except yaml.YAMLError as exc:
        raise CardError(f"invalid YAML: {exc}") from exc
    validate_card(card)
    return card


def project(repo: dict, card: dict, provenance: str) -> dict:
    """Project a parsed Card into a single index row."""
    validate_card(card)
    owner, name = repo["owner"], repo["name"]
    structured = card.get("x-fabric-card")
    card_url = f"https://github.com/{owner}/{name}/wiki/Card_{name}"
    if structured and structured["card_url"] != card_url:
        raise CardError(
            f"x-fabric-card.card_url: must match discovered card {card_url}"
        )
    x = card.get("x-llm-wiki") or {}
    topics = x.get("topics") or card.get("topics") or []
    endpoints = x.get("endpoints") or {}
    row = {
        "id": card.get("id") or f"{owner}/{name}",
        "owner_repo": f"{owner}/{name}",
        "description": (card.get("description") or "").strip(),
        "topics": topics,
        "capabilities": card.get("capabilities") or [],
        "home_url": f"https://github.com/{owner}/{name}/wiki/Home_{name}",
        "card_url": f"https://github.com/{owner}/{name}/wiki/Card_{name}",
        "wiki_clone_url": f"https://github.com/{owner}/{name}.wiki.git",
        "endpoints": endpoints,
        "private": repo.get("private", False),
        "provenance": provenance,
    }
    if structured:
        row.update(
            {
                "id": structured["id"],
                "description": structured["description"].strip(),
                "capabilities": [
                    skill["description"] for skill in structured["skills"]
                ],
                "x-fabric-card": structured,
            }
        )
    return row


def main() -> int:
    org_repos = list_org_repos()
    topic_repos = list_topic_repos()

    # Dedupe: org entries win for any (owner, name) collision.
    org_keys = {(r["owner"], r["name"]) for r in org_repos}
    topic_only = [r for r in topic_repos if (r["owner"], r["name"]) not in org_keys]

    print(
        f"Walking {len(org_repos)} org repos + {len(topic_only)} topic-only repos "
        f"(trusted owners: {sorted(TRUSTED_TOPIC_OWNERS)})",
        file=sys.stderr,
    )

    agents: list[dict] = []
    for repo, provenance in [(r, "org") for r in org_repos] + [
        (r, "topic") for r in topic_only
    ]:
        owner, name = repo["owner"], repo["name"]
        print(f"  {owner}/{name} (via {provenance})...", file=sys.stderr)
        with tempfile.TemporaryDirectory() as td:
            wiki_dir = Path(td)
            if not clone_wiki(repo, wiki_dir):
                continue
            card_path = wiki_dir / f"Card_{name}.md"
            if not card_path.exists():
                print(
                    f"  {owner}/{name}: no Card_{name}.md in wiki; skipping",
                    file=sys.stderr,
                )
                continue
            try:
                card = parse_card(card_path)
                row = project(repo, card, provenance)
                if any(entry["id"] == row["id"] for entry in agents):
                    raise CardError(f"id: duplicate indexed identity {row['id']!r}")
            except (CardError, UnicodeError) as exc:
                print(
                    f"  {owner}/{name} / {card_path.name}: {exc}; skipping",
                    file=sys.stderr,
                )
                continue
            agents.append(row)

    index = {
        "schema_version": INDEX_SCHEMA_VERSION,  # additive structured card payload
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "build-index.py via .github/workflows/build-index.yml",
        "org": ORG,
        "discovery": {
            "topic": TOPIC_NAME,
            "trusted_topic_owners": sorted(TRUSTED_TOPIC_OWNERS),
        },
        "agents": sorted(agents, key=lambda a: a["id"]),
    }

    INDEX_PATH.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {INDEX_PATH} with {len(agents)} agents", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
