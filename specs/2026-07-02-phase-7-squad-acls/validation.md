# Phase 7 — Squad-level ACLs (Tier-1) — validation

## Definition of done (roadmap alignment)

From `specs/roadmap.md` Phase 7:

> **Done when:** A user in squad-payments sees private payments channel results; a user outside does not.

From `specs/2026-07-02-phase-7-squad-acls/requirements.md` (Acceptance):

> A user in `squad-payments` (valid Keycloak token) retrieves chunks tagged `squad-payments`/`tier-1`; the same query without a token, or with a token from a different squad, does not return them — proven by integration tests against the live Keycloak container.

This document records the actual commands and outputs from running that proof end-to-end on `phase-7-squad-acls`.

## Stack health

```
$ docker compose ps --format "table {{.Name}}\t{{.Status}}"
NAME                    STATUS
team-rag-keycloak-1     Up 21 minutes (healthy)
team-rag-open-webui-1   Up 21 minutes (healthy)
team-rag-postgres-1     Up 23 minutes (healthy)
team-rag-qdrant-1       Up 21 minutes (healthy)
team-rag-tei-1          Up 21 minutes (healthy)
```

Keycloak realm `teamrag` is seeded via `keycloak/realm-teamrag.json` (`--import-realm`), with client `teamrag-gateway`, groups `squad-payments` / `squad-platform`, and dev users `alice` (member of `squad-payments`) / `bob` (no groups).

## Automated checks — full suite

```bash
export DATABASE_URL=postgresql+asyncpg://teamrag:teamrag@localhost:5432/teamrag
uv run pytest tests/ -v
```

Result:

```
================= 108 passed, 2 skipped, 24 warnings in 22.68s =================
```

The 2 skips are the LLM-dependent Phase 3 tests (`test_chat_completions_returns_200`, `test_chat_completions_includes_source_url`) — expected, since no `LLM_BASE_URL`/`LLM_API_KEY` is configured in this environment; unrelated to Phase 7.

All 5 Phase 7 tests in `tests/integration/test_phase7_squad_acls.py` passed:

```
tests/integration/test_phase7_squad_acls.py::test_squad_member_sees_tier1_chunk PASSED
tests/integration/test_phase7_squad_acls.py::test_anonymous_cannot_see_tier1_chunk PASSED
tests/integration/test_phase7_squad_acls.py::test_other_squad_user_cannot_see_tier1_chunk PASSED
tests/integration/test_phase7_squad_acls.py::test_invalid_token_is_401 PASSED
tests/integration/test_phase7_squad_acls.py::test_audit_log_records_squad_query PASSED
======================== 5 passed, 5 warnings in 1.88s =========================
```

The one warning repeated per test is a benign Qdrant client/server minor-version mismatch notice (client 1.17.1 vs server 1.13.1), pre-existing across all suites, not a Phase 7 regression.

## Roadmap "done when" evidence

Each assertion below is backed by a passing test in `tests/integration/test_phase7_squad_acls.py`, run against the live Keycloak + Qdrant + Postgres stack (no mocking of the IdP or vector store):

1. **Squad member sees the private chunk.** `test_squad_member_sees_tier1_chunk` obtains a real access token for `alice` via Keycloak's password grant (`POST /realms/teamrag/protocol/openid-connect/token`), seeds one Qdrant point tagged `acl_tags=["squad-payments","tier-1"]`, calls `POST /query` with `Authorization: Bearer <alice token>`, and asserts the seeded `source_url` (`https://example.com/private/payments-rollout`) **is** present in the response's chunk list. PASSED.

2. **Anonymous caller does not see it.** `test_anonymous_cannot_see_tier1_chunk` issues the same `POST /query` with **no** `Authorization` header and asserts the response is `200` (Phase 5 unauthenticated-tier-0 behavior preserved) and the tier-1 `source_url` is **absent**. PASSED.

3. **Different-squad user does not see it.** `test_other_squad_user_cannot_see_tier1_chunk` obtains a token for `bob` (no group memberships in Keycloak) and asserts the tier-1 `source_url` is **absent** from `bob`'s results. PASSED.

4. **Invalid token is rejected, not silently downgraded.** `test_invalid_token_is_401` sends `Authorization: Bearer garbage.token.here` and asserts `POST /query` returns **401** (does not fall back to tier-0-only 200). PASSED.

5. **Audit trail records the authenticated identity and applied ACL tags.** `test_audit_log_records_squad_query` runs a marker query as `alice`, then reads the corresponding `audit_log` row directly from Postgres and asserts `caller_id != "anonymous"` and `"squad-payments" in acl_tags_applied`. PASSED.

Together these satisfy both the roadmap's one-line "done when" and the requirements doc's more detailed acceptance criterion (positive control, negative control across two axes — no token and wrong squad — plus the 401 contract and audit evidence).

## Sync-job smoke (`python -m teamrag.sync --seed`)

```bash
$ export DATABASE_URL=postgresql+asyncpg://teamrag:teamrag@localhost:5432/teamrag
$ uv run python -m teamrag.sync --seed "github:org/payments-svc:squad-payments,tier-1"
2026-07-02 19:27:34,767 INFO __main__: Seeding 1 mapping(s) from CLI args
2026-07-02 19:27:34,824 INFO __main__: Upserted 1 resource ACL mapping(s)
```

Verification via psql:

```bash
$ docker compose exec postgres psql -U teamrag -c "select source_type, resource_key, acl_tags from resource_acl_mappings;"
 source_type |   resource_key   |        acl_tags
-------------+------------------+-------------------------
 github      | org/payments-svc | {squad-payments,tier-1}
(1 row)
```

Row shape matches the `--seed source_type:resource_key:tag1,tag2` argument: `source_type=github`, `resource_key=org/payments-svc`, `acl_tags={squad-payments,tier-1}`.

Cleanup (to keep the table empty for reruns):

```bash
$ docker compose exec postgres psql -U teamrag -c "delete from resource_acl_mappings where resource_key='org/payments-svc';"
DELETE 1
$ docker compose exec postgres psql -U teamrag -c "select source_type, resource_key, acl_tags from resource_acl_mappings;"
 source_type | resource_key | acl_tags
-------------+--------------+----------
(0 rows)
```

## Manual checklist

1. **Stack health:** `docker compose ps` — postgres, qdrant, tei, keycloak, open-webui all `healthy`. ✅ (see above)
2. **Positive control (alice / squad-payments):** confirmed via `test_squad_member_sees_tier1_chunk`. ✅
3. **Negative control — no token:** confirmed via `test_anonymous_cannot_see_tier1_chunk`. ✅
4. **Negative control — wrong squad (bob):** confirmed via `test_other_squad_user_cannot_see_tier1_chunk`. ✅
5. **401 contract for invalid token:** confirmed via `test_invalid_token_is_401`. ✅
6. **Audit log correctness:** confirmed via `test_audit_log_records_squad_query` (direct Postgres read, not just HTTP status). ✅
7. **Sync job (seed path):** ran `python -m teamrag.sync --seed ...`, verified the row via `psql`, cleaned it up afterward. ✅
8. **Full regression suite:** `uv run pytest tests/ -v` — 108 passed, 2 skipped (LLM-dependent, expected), 0 failed. ✅

## Merge approval

- Implementing engineer self-certifies automated + manual sections above (this document) and the passing `uv run pytest tests/ -v` run.
- Reviewer confirms scope matches `specs/2026-07-02-phase-7-squad-acls/requirements.md`: Keycloak fixture (T1), JWT validation + 401 contract (T2/T4), `AUTHENTICATED_GROUPS` filter mode (T3), audit of `caller_id`/`acl_tags_applied` (T6), `resource_acl_mappings` + sync job incl. `--seed` (T7–T9), and this acceptance test (T11) — all demonstrated against the live stack, not mocks.
