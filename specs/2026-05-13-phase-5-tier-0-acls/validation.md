# Phase 5 — Tier-0 ACLs — validation

## Definition of done (roadmap alignment)

From `specs/roadmap.md` Phase 5:

> **Done when:** A query from an unauthenticated caller cannot retrieve private content even if it exists in Qdrant.

For this spec cycle, **“private content”** means any vector point that is **not** eligible under the **tier-0** filter (for example, points deliberately seeded with tags that exclude `tier-0`, or future non–tier-0 tags used in tests).

## Automated checks (required before merge)

- **Unit tests** pass for tag constants/helpers and ingestion writers (tag presence on chunk create / upsert).
- **Integration tests** pass against Docker-backed Qdrant + Postgres (project standard):
  - Seed at least one point (or DB row) that would match semantically but **must not** be returned to an **unauthenticated** `POST /query` because its `acl_tags` do not satisfy the tier-0 constraint.
  - Assert the response contains **no** forbidden chunk IDs / payloads.
- If MCP wraps retrieval: extend or add integration/smoke coverage so **MCP `search_knowledge`** (or equivalent) cannot return the same forbidden chunks without identity.

CI should be green on the implementing branch (`uv run pytest` or the repo’s documented CI command).

## Manual checklist (required before merge)

Record commands and outcomes in the PR description.

1. **Stack health:** `docker compose ps` shows Postgres, Qdrant, and TEI healthy (per `CLAUDE.md` / README).
2. **Unauth API:** `curl` `POST /query` with a query text known to hit seeded **non–tier-0-only** content; confirm **no** such chunks in JSON (empty `chunks` or only tier-0-tagged items, per implementation).
3. **Unauth MCP (if applicable):** Run the documented MCP smoke or local client flow against the same backend revision; confirm the forbidden document does not surface in tool results.
4. **Positive control:** With a **tier-0-only** corpus (or test data), confirm a benign query still returns expected chunks so the filter is not overly broken.

## Merge approval

- Implementing engineer self-certifies automated + manual sections above.
- Reviewer confirms scope matches `requirements.md` (especially **unauthenticated-only** contract for Phase 5).
