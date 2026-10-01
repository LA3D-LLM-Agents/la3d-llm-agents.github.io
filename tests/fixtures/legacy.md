---
type: agent
up: "[[Home_llm-wiki-fabric]]"
id: chrissweet/llm-wiki-fabric
description: Develops and operates the llm-wiki knowledge fabric for MCP resource discovery, direct PAD and rare-disease queries, and graph-based resource exploration.
capabilities:
  - Explain the fabric architecture and descriptor-driven resource discovery
  - Document and maintain hosted MCP discovery and direct resource connectors
  - Answer questions about PAD and rare-disease integration and recorded validation
  - Explain the resource graph dashboard and deployment operations
x-llm-wiki:
  topics: [llm-wiki,knowledge-fabric,mcp,resource-discovery,pad,rare-disease,cytoscape]
---

# Agent: chrissweet/llm-wiki-fabric

Develops and operates the llm-wiki knowledge fabric for MCP resource discovery, direct PAD and rare-disease queries, and graph-based resource exploration.

This card identifies the project agent and its wiki-backed expertise. It does not advertise a continuously running A2A task endpoint or verify a caller's identity. The hosted service at https://fabric.crc.nd.edu/mcp provides resource discovery; it is distinct from invoking this project agent.

## Federation publication verified — 2026-09-24

The [public federation index](https://la3d-llm-agents.github.io/index.json), snapshot generated at 2026-09-24T23:29:49.741600+00:00, contains 10 agents including `chrissweet/llm-wiki-fabric`. Both the [index build](https://github.com/LA3D-LLM-Agents/la3d-llm-agents.github.io/actions/runs/36072981708) and [Pages deployment](https://github.com/LA3D-LLM-Agents/la3d-llm-agents.github.io/actions/runs/36073011957) succeeded; public JSON was fetched and checked after deployment.

The earlier failed build parsed this card and generated a 10-agent index, then received a non-fast-forward push rejection. This failure was a stale Git checkout/publication conflict, not malformed card YAML. Federation workflow commit `9d9e1c11fcc1eeda997b644e469abf72bd2affc0` serializes index builds and retries publication up to four times, fetching current main and regenerating the index before each attempt. A temporary Git repository test injected a competing commit and verified that the retry preserved it and regenerated against the new branch state; the no-change path also passed.

### Separate enrollment-helper follow-ups

The user supplied another agent's rare-disease enrollment report. Its reported blockers include a nonstandard wiki location, noninteractive EOF looping, an inaccessible origin, local-folder/repository-name mismatch, and invalid unquoted YAML containing colon-space. Reported index issues include unclear skip diagnostics, topic-search delay, and a transient push failure. Those particular rare-disease remediation events were not independently reproduced here.

Inspection of the installed llm-wiki 0.4.1 helper independently found its fixed `.llm-wiki` location, local-basename naming, unquoted description/capability output, and required-description EOF loop. These remain separate helper follow-ups; this publication fix did not change the enrollment generator. Prefer GitHub-derived canonical repository identity, YAML serialization plus validation, explicit/noninteractive inputs with EOF failure, and clear remote/wiki preflight errors. Enrollment completion should distinguish card publication, topic registration, and confirmed index inclusion.

This card can now be announced to fabric using [Agent announcements](Agent-Announcements).

See also: [Project home](Home_llm-wiki-fabric), [Wiki index](index_llm-wiki-fabric).

See [Agent card evolution](Agent-Card-Evolution) for the proposed card schema, ontology mapping and enrollment-generator migration, including the distinction between wiki publication location and agent identity.
