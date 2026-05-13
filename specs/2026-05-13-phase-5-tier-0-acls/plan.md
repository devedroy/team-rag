# Phase 5 — Tier-0 ACLs — plan

Numbered groups are **sequenced**; within a group, tasks may proceed in parallel where safe.

## 1. Schema and ingestion tagging (first)

1.1. Audit `sources` / `chunks` / `acl_tags` usage in code and migrations; list every writer path that creates chunks (ingest pipelines, any admin or test fixtures).

1.2. Define the canonical **tier-0** tag string and document it in code (single source of truth constant or enum).

1.3. Implement ingestion-time association so new chunks receive **`tier-0`** in Postgres (`acl_tags` / join table) for content that belongs in the public tier per roadmap.

1.4. Extend or verify Qdrant upsert payloads so each point includes **`acl_tags`** (or equivalent field name used by the filter DSL) consistent with Postgres.

1.5. Add unit tests for tagging helpers and for “ingest writes expected tags” on representative payloads.

## 2. Read path — unauthenticated filter (after payloads are correct)

2.1. In the FastAPI retrieval layer used by `POST /query`, build a Qdrant **filter** that restricts results to points whose `acl_tags` contain **`tier-0`** when the request is **unauthenticated** (no user identity attached).

2.2. Ensure the same filter semantics apply to any internal search helper reused by MCP (`search_knowledge`, etc.) so MCP cannot bypass ACL.

2.3. Return empty lists (not errors) when no points match, preserving existing API shapes unless a dedicated error contract already exists for auth failures.

2.4. Add integration tests: seeded non–tier-0 points must **never** appear in unauthenticated query results.

## 3. Observability and safety checks

3.1. Log at **info** or **debug** (no sensitive content) when ACL filter mode is `unauthenticated_tier0` vs future authenticated modes stubbed out.

3.2. Document in code comments or module docstring that authenticated group resolution is intentionally **not** implemented in Phase 5.

## 4. Backfill and operations (as needed)

4.1. If production-like data exists without tags, add a one-off migration or documented re-ingest procedure to attach **`tier-0`** where appropriate.

4.2. Update `.env.example` only if new toggles are required (e.g., strict mode flag); prefer zero new env vars if behavior is fully determined by presence/absence of identity.

## 5. Final verification gate

5.1. Complete automated test suite relevant to query + MCP retrieval.

5.2. Execute the manual checklist in `validation.md` and attach evidence (paste output or CI links) to the implementing PR.
