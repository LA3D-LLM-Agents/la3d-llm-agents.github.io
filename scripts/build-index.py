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
- Cards whose frontmatter cannot be parsed
- Internal infrastructure repos (.github, the Pages repo itself)
- Topic-tagged repos whose owner is NOT in the allowlist

The script is intended to be run from the repo root of
la3d-llm-agents.github.io; it writes ./index.json.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

ORG = "LA3D-LLM-Agents"
INDEX_PATH = Path("index.json")
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
    "LA3D-LLM-Agents",       # ourselves; redundant since org-walk covers, kept for clarity
    "LA3D",                  # the broader LA3D org
    "crcresearch",           # the template owner
    "PaperAnalyticalDeviceND",  # Priscila + Maximilian's PAD/chemopad domain
    "chrissweet",            # personal
    "charlesvardeman",       # LA3D co-owner
    "psaboia",               # Priscila Saboia Moreira
}
TRUSTED_TOPIC_OWNERS_LC = {o.lower() for o in TRUSTED_TOPIC_OWNERS}


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
                "gh", "search", "repos",
                "--topic", TOPIC_NAME,
                "--limit", "100",
                "--json", "fullName,owner,isPrivate,isArchived",
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
        out_repos.append({
            "name": name,
            "owner": owner,
            "private": bool(r.get("isPrivate", False)),
        })
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


def parse_card(card_path: Path) -> dict | None:
    """Parse the frontmatter block of a Card_<repo>.md file."""
    text = card_path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    try:
        return yaml.safe_load(text[4:end])
    except yaml.YAMLError as e:
        print(f"  YAML parse error in {card_path}: {e}", file=sys.stderr)
        return None


def project(repo: dict, card: dict, provenance: str) -> dict:
    """Project a parsed Card into a single index row."""
    owner, name = repo["owner"], repo["name"]
    x = card.get("x-llm-wiki") or {}
    topics = x.get("topics") or card.get("topics") or []
    endpoints = x.get("endpoints") or {}
    return {
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
    for repo, provenance in (
        [(r, "org") for r in org_repos] +
        [(r, "topic") for r in topic_only]
    ):
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
            card = parse_card(card_path)
            if card is None:
                print(
                    f"  {owner}/{name}: could not parse Card frontmatter; skipping",
                    file=sys.stderr,
                )
                continue
            agents.append(project(repo, card, provenance))

    index = {
        "schema_version": "0.2.0",  # bumped: index entries now carry `provenance`
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
