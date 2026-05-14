# Phase 5 — Tier-0 ACLs — requirements

## Context

TeamRag’s mission treats retrieval as the shared knowledge layer for humans and MCP clients; access must be enforced **before** chunks reach any LLM (`specs/mission.md`). The tech stack commits to Qdrant for vector search with filtering, Postgres for ACL-related metadata, and **retrieval-time** enforcement (`specs/tech-stack.md`).

Roadmap Phase 5 defines the first ACL slice: **tier-0** tagging, query-time filtering with `acl_tags ∩ user_groups ≠ ∅`, Qdrant filter DSL from FastAPI, and the guarantee that **unauthenticated** callers only see tier-0 content (`specs/roadmap.md`).

## Scope (this feature)

- Ensure every **newly ingested** chunk carries `acl_tags` including **`tier-0`** where the roadmap expects public/engineering-wide content (exact sources in scope follow existing ingestion phases already in the repo).
- Persist ACL tag associations in Postgres (chunk ↔ tags) consistent with the Phase 0 schema direction and any existing models.
- Ensure Qdrant point payloads include the information needed to filter by `acl_tags` (aligned with Qdrant filter DSL).
- Wire **unauthenticated** `POST /query` (and MCP tools that call the same retrieval path) so vectors are filtered to **tier-0 only** — callers without identity MUST NOT receive chunks that are not tier-0-eligible, even if such points exist in the collection.

## Explicitly out of scope (deferred)

Per stakeholder direction for this spec cycle:

- **Authenticated callers and real `user_groups`** — no Keycloak/Okta sync, no JWT contract, no “squad” resolution. Any future behavior where `user_groups` comes from IdP belongs in a **follow-up spec** (roadmap Phase 7 and related).
- **Tier-1 / Tier-2** tagging rules, channel/repo-to-squad mappings, and restricted allowlists.
- Changing the embedding model, reranking, or new data sources beyond what is required to attach tags and filters.

## Decisions

1. **Narrow Phase 5 contract:** The only supported identity posture for merge is **unauthenticated = tier-0 visibility**. Other identity modes are documented as future work only.
2. **Ordering:** **Ingestion and payload correctness first** — tag chunks and align DB + Qdrant payloads before tightening the read path, so tests can prove end-to-end tagging before filter enforcement lands.
3. **Enforcement point:** Filters apply in the retrieval gateway (FastAPI) using Qdrant’s filter DSL; MCP remains a thin client over that API (`specs/tech-stack.md`).

## Dependencies / assumptions

- Phase 0 infrastructure (Postgres, Qdrant, TEI) and prior phases that write chunks remain healthy.
- MCP server continues to delegate retrieval to the same backend behavior as `POST /query` so ACL semantics stay unified.

## Open questions (non-blocking for drafting; track in implementation PRs)

- Exact default tag set for legacy rows if any exist without `acl_tags` (migration vs. re-ingest).
- Whether tier-0 is represented as a single tag string `tier-0` or a structured enum in code (must match Qdrant payload and Postgres).
