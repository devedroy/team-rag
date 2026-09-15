# Phase 7 — Squad-level ACLs (Tier-1) — requirements

## Context

Phase 5 shipped the unauthenticated = tier-0 posture: every retrieval path
(`POST /query`, `GET /document`, `/v1/chat/completions`, MCP tools) filters
Qdrant to `tier-0` (or missing) `acl_tags`. `resolve_acl_filter_mode_from_request`
in `src/teamrag/acl.py` is the designated extension point for identity
(see the Phase 5 spec, `specs/2026-05-13-phase-5-tier-0-acls/`).

Roadmap Phase 7 gates private content to squad members: IdP group sync, a
nightly job refreshing resource→squad mappings in Postgres, tier-1 tagging of
private channels/repos, and per-user group lookup injected into every query
filter (`specs/roadmap.md`).

## Scope (this feature)

- **Keycloak** added to Docker Compose (`start-dev`, local only) with a
  committed realm import (`keycloak/realm-teamrag.json`): realm `teamrag`,
  client `teamrag-gateway`, sample squad groups, test users, and a group
  membership mapper putting `groups` in the access token.
- **JWT validation** in the gateway (`src/teamrag/auth.py`): `Authorization:
  Bearer <JWT>` verified against Keycloak JWKS (cached); extracts `sub` and
  `groups`.
  - No token → unauthenticated, tier-0-only (Phase 5 behavior unchanged).
  - Invalid/expired token → **401**.
  - Valid token → visibility is `tier-0 ∪ user's groups`.
- **New ACL filter mode** `AUTHENTICATED_GROUPS`: Qdrant filter
  `should=[acl_tags=tier-0, acl_tags is-empty, acl_tags ∈ user_groups]`.
- **Postgres mapping table** `resource_acl_mappings`
  (`source_type`, `resource_key`, `acl_tags`, `updated_at`) via Alembic.
  Ingest connectors (GitHub, Confluence, Teams, Webex) look up the mapping
  for their resource key and tag chunks accordingly; no match → tier-0
  default (current behavior).
- **Sync job** `python -m teamrag.sync`: pulls Keycloak groups and their
  attributes (`repos`, `channels`, `rooms`, `spaces`) via the admin API and
  upserts `resource_acl_mappings`. Cron-able; also supports direct seeding
  flags for development.
- **Audit**: `audit_log` rows record the caller's `sub` and applied groups.
- **MCP**: gateway client forwards an optional `TEAMRAG_BEARER_TOKEN`.

## Explicitly out of scope (deferred)

- Tier-2 / restricted allowlists (roadmap "Deferred").
- Okta / Google Workspace backends (Keycloak only; the JWKS URL is a
  hardcoded Keycloak-shaped path — `{OIDC_ISSUER}/protocol/openid-connect/certs`
  — derived from `OIDC_ISSUER`, not discovered. Generic `.well-known`
  OIDC discovery so other IdPs can slot in later is future work).
- SSO for Open WebUI itself; only the gateway API enforces identity.
- Re-tagging of already-ingested chunks (re-ingest is the supported path).

## Decisions

1. **Groups come from the validated JWT claim**, not a per-request Postgres
   lookup — Keycloak's group mapper keeps tokens authoritative and fresh at
   token TTL granularity. Postgres holds **resource→tag** mappings (ingest
   side), refreshed by the sync job. This satisfies the roadmap's
   "per-user group lookup injected into every query filter" via the token.
2. **Absent token stays 200/tier-0**, matching the Phase 5 contract;
   presenting a bad token is an error (401) rather than silent downgrade.
3. **Group tag == ACL tag string** (e.g. Keycloak group `squad-payments`
   maps to `acl_tags` value `squad-payments`); no translation layer.
4. Enforcement stays in the FastAPI gateway using Qdrant filter DSL; MCP
   remains a thin client (unchanged principle from Phase 5).

## Dependencies / assumptions

- Phases 0–6 healthy (Postgres, Qdrant, TEI, connectors, MCP).
- New dependency: `pyjwt[crypto]` for JWKS/JWT verification.
- Keycloak container is a dev/test fixture; production deployments point
  the same env vars at a managed IdP.

## Acceptance (roadmap "Done when")

A user in `squad-payments` (valid Keycloak token) retrieves chunks tagged
`squad-payments`/`tier-1`; the same query without a token, or with a token
from a different squad, does not return them — proven by integration tests
against the live Keycloak container.
