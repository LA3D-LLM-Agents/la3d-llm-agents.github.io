# Federation index builder

The hourly workflow discovers trusted repositories, reads their GitHub wiki
`Card_<repo>.md` pages, and publishes `index.json` for federation clients.

## Agent-card reader migration

Index schema **0.3.0** adds an optional `x-fabric-card` payload to each agent row.
All existing row keys remain present. This index version is independent of card
schema **1.0** and fabric ontology **0.3.0**.

- Legacy cards retain their existing projection and repository-derived URLs.
- Structured-only cards project `id`, `description`, and skill descriptions into
  the existing `capabilities` list. The full validated `x-fabric-card` is preserved.
- Transitional cards must have matching top-level IDs, descriptions and capability
  lists wherever these are also declared. Conflicts reject the card; there is no
  silent precedence rule. Description equality is checked before whitespace
  trimming; capability order is significant.
- Agent-level topics still come from `x-llm-wiki.topics`, with the existing
  top-level fallback. Skill tags do not become agent topics.
- Established agent IDs may differ from the repository owner/name. IDs and skill
  IDs are never regenerated. The structured `card_url` must match the discovered
  GitHub wiki card location, preventing contradictory publication identities.
- Existing `home_url`, `wiki_clone_url`, and `endpoints` semantics are unchanged.
  Explicit interfaces survive in the structured block; they do not overwrite the
  legacy repository-derived clone URL or imply an A2A endpoint. New consumers
  should use the structured interfaces to select explicit invocation routes.
- Malformed YAML, duplicate keys, incorrect field types, unsupported versions,
  duplicate skill/bundle/interface IDs, unknown bundle references, conflicting
  representations, and duplicate indexed agent identities produce a repository,
  card and field diagnostic on stderr. Invalid cards are skipped while others
  continue. For duplicate agent IDs the first valid discovery wins (org before
  topic); the later card is diagnosed and skipped.

The vendored `schemas/agent-card-1.0.schema.json` is copied unchanged from
[LA3D-LLM-Agents/ns at 7650c279](https://github.com/LA3D-LLM-Agents/ns/blob/7650c279b1cc03750cc14722460d78030f86c8e6/examples/agent-card.schema.json).
Validation is local and does not fetch schema URLs. Update the vendored schema
and tests deliberately when supporting a new version.

## Test and run

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
# Uses gh authentication; writes index.json but does not commit or publish:
python scripts/build-index.py
```

Offline tests cover old, transitional and structured-only cards, mixed index
builds and actionable failures. The legacy and transitional fixtures come from
the fabric project's reviewed migration example; `legacy-index-row.json` is its
row in index commit `81c8131` (snapshot 2026-10-01T21:17:54.621041+00:00).
The legacy row is compared exactly, including repository privacy and provenance.
Current Pages and wiki-ask clients use the retained legacy fields. The fabric
project separately checks the generated rows against its existing importer.
No blanket compatibility claim is made for consumers that reject unfamiliar
index versions or extra fields.

This change upgrades the reader only. Enrollment, wiki cards, fabric RDF mapping
and live deployment are separate steps. Deploy compatible readers before new
cards are published.
