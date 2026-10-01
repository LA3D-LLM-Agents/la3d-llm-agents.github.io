"""Offline reader contracts; run with python -m unittest discover -s tests -v."""

import copy
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "build_index", ROOT / "scripts/build-index.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
REPO = {"owner": "chrissweet", "name": "llm-wiki-fabric", "private": True}


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.legacy = builder.parse_card(ROOT / "tests/fixtures/legacy.md")
        self.transitional = builder.parse_card(ROOT / "tests/fixtures/transitional.md")
        self.structured = {
            "x-fabric-card": copy.deepcopy(self.transitional["x-fabric-card"])
        }

    def project(self, card):
        return builder.project(REPO, card, "topic")

    def test_legacy_matches_recorded_public_index(self):
        index = json.loads((ROOT / "tests/fixtures/legacy-index-row.json").read_text())
        self.assertEqual(self.project(self.legacy), index)

    def test_transitional_preserves_every_legacy_field(self):
        row = self.project(self.transitional)
        self.assertEqual(row.pop("x-fabric-card"), self.structured["x-fabric-card"])
        self.assertEqual(row, self.project(self.legacy))

    def test_structured_only_projects_skills_and_retains_routes(self):
        row = self.project(self.structured)
        old = self.project(self.legacy)
        for key in (
            "id",
            "description",
            "capabilities",
            "wiki_clone_url",
            "card_url",
            "home_url",
        ):
            self.assertEqual(row[key], old[key])
        self.assertEqual(row["topics"], [])  # skill tags are not agent topics
        self.assertEqual(row["x-fabric-card"], self.structured["x-fabric-card"])
        self.assertEqual(row["endpoints"], {})  # no invented A2A or MCP endpoint

    def test_existing_identity_need_not_match_repository(self):
        for card in (self.legacy, self.transitional, self.structured):
            if "id" in card:
                card["id"] = "historic-owner/stable-agent"
            if "x-fabric-card" in card:
                card["x-fabric-card"]["id"] = "historic-owner/stable-agent"
            row = self.project(card)
            self.assertEqual(row["id"], "historic-owner/stable-agent")
            self.assertEqual(row["owner_repo"], "chrissweet/llm-wiki-fabric")

    def test_legacy_defaults_and_topics_precedence(self):
        row = self.project({})
        self.assertEqual(row["id"], "chrissweet/llm-wiki-fabric")
        self.assertEqual(row["description"], "")
        self.assertEqual(row["capabilities"], [])
        for nested, expected in ((["nested"], ["nested"]), ([], ["fallback"])):
            row = self.project(
                {
                    "topics": ["fallback"],
                    "x-llm-wiki": {"topics": nested, "endpoints": {"ask": "custom"}},
                }
            )
            self.assertEqual(row["topics"], expected)
            self.assertEqual(row["endpoints"], {"ask": "custom"})

    def test_rejects_legacy_type_errors(self):
        for card in (
            [],
            None,
            "scalar",
            {"id": 12},
            {"id": "bad"},
            {"description": {}},
            {"capabilities": [{"Read": "data"}]},
            {"topics": "topic"},
            {"x-llm-wiki": []},
            {"x-llm-wiki": {"topics": 2}},
            {"x-llm-wiki": {"endpoints": []}},
            {"x-fabric-card": None},
        ):
            with self.subTest(card=card), self.assertRaises(builder.CardError):
                self.project(card)

    def test_rejects_conflicting_dual_fields(self):
        for key, value in (
            ("id", "other/identity"),
            ("description", "different"),
            ("capabilities", []),
        ):
            card = copy.deepcopy(self.transitional)
            card[key] = value
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(builder.CardError, f"{key}: conflicts"),
            ):
                self.project(card)

    def test_rejects_schema_errors(self):
        for key, value in (
            ("schema_version", "2.0"),
            ("skills", []),
            ("interfaces", [{}]),
            ("name", ""),
            ("knowledge_bundles", "bundle"),
            ("surprise", True),
        ):
            card = copy.deepcopy(self.structured)
            card["x-fabric-card"][key] = value
            with self.subTest(key=key), self.assertRaises(builder.CardError):
                self.project(card)
        del self.structured["x-fabric-card"]["id"]
        with self.assertRaisesRegex(builder.CardError, "required property"):
            self.project(self.structured)

    def test_rejects_duplicate_ids(self):
        for key in ("skills", "knowledge_bundles", "interfaces"):
            card = copy.deepcopy(self.structured)
            card["x-fabric-card"][key].append(
                copy.deepcopy(card["x-fabric-card"][key][0])
            )
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(builder.CardError, f"{key}: duplicate id"),
            ):
                self.project(card)

    def test_rejects_missing_bundle_reference_and_wrong_card_location(self):
        self.structured["x-fabric-card"]["interfaces"][0]["bundle_id"] = "absent"
        with self.assertRaisesRegex(builder.CardError, "unknown bundle_id"):
            self.project(self.structured)
        self.structured["x-fabric-card"]["interfaces"] = []
        self.structured["x-fabric-card"]["card_url"] = "https://example.org/card"
        with self.assertRaisesRegex(builder.CardError, "must match discovered card"):
            self.project(self.structured)

    def test_preserves_explicit_interfaces_without_rewriting_legacy_clone_route(self):
        interfaces = self.structured["x-fabric-card"]["interfaces"]
        interfaces[0]["url"] = "https://example.org/another-wiki.git"
        interfaces.extend(
            [
                {
                    "id": "rpc",
                    "kind": "mcp",
                    "transport": "streamable-http",
                    "url": "https://example.org/mcp",
                },
                {
                    "id": "tasks",
                    "kind": "a2a",
                    "transport": "jsonrpc",
                    "protocol_version": "0.3",
                    "url": "https://example.org/tasks",
                },
            ]
        )
        row = self.project(self.structured)
        self.assertEqual(row["x-fabric-card"]["interfaces"], interfaces)
        self.assertEqual(
            row["wiki_clone_url"],
            "https://github.com/chrissweet/llm-wiki-fabric.wiki.git",
        )

    def test_parser_rejects_bad_frontmatter_and_duplicate_keys(self):
        for text in (
            "no delimiters",
            "---\nid: x/y",
            "---\n[]\n---\n",
            "---\n- item\n---\n",
            "---\nid: a/b\nid: c/d\n---\n",
            "---\nx: [\n---\n",
            "---\nx-llm-wiki:\n  topics: []\n  topics: [other]\n---\n",
        ):
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "Card.md"
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(builder.CardError):
                    builder.parse_card(path)

    def test_mixed_build_continues_after_invalid_card_and_duplicate_identity(self):
        repos = [
            {"owner": "org", "name": n}
            for n in ("bad", "legacy", "new", "dual", "duplicate")
        ]

        def clone(repo, dest):
            name = repo["name"]
            if name == "bad":
                text = "---\ndescription: []\n---\n"
            else:
                card = copy.deepcopy(
                    self.legacy
                    if name in ("legacy", "duplicate")
                    else self.structured
                    if name == "new"
                    else self.transitional
                )
                identity = "org/legacy" if name == "duplicate" else f"org/{name}"
                if "id" in card:
                    card["id"] = identity
                if "x-fabric-card" in card:
                    card["x-fabric-card"]["id"] = identity
                    card["x-fabric-card"]["card_url"] = (
                        f"https://github.com/org/{name}/wiki/Card_{name}"
                    )
                text = "---\n" + builder.yaml.safe_dump(card) + "---\n"
            (dest / f"Card_{name}.md").write_text(text)
            return True

        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(builder, "list_org_repos", return_value=repos),
            patch.object(builder, "list_topic_repos", return_value=[]),
            patch.object(builder, "clone_wiki", side_effect=clone),
            patch.object(builder, "INDEX_PATH", Path(td) / "index.json"),
            redirect_stderr(io.StringIO()) as log,
        ):
            self.assertEqual(builder.main(), 0)
            data = json.loads(builder.INDEX_PATH.read_text())
            self.assertEqual(data["schema_version"], "0.3.0")
            self.assertEqual(
                [a["id"] for a in data["agents"]], ["org/dual", "org/legacy", "org/new"]
            )
            self.assertIn(
                "org/bad / Card_bad.md: description: expected a string; skipping",
                log.getvalue(),
            )
            self.assertIn("duplicate indexed identity", log.getvalue())


if __name__ == "__main__":
    unittest.main()
