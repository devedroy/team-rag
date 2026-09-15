# Phase 8 — Jira ingest → retrieve → cite — requirements

## Context

Roadmap Phase 8 unlocks close-ticket knowledge ("have we tried this
before?"): one chunk per ticket = title + description + all comments +
resolution; metadata `status`, `assignee`, `reporter`, `labels`, `epic`,
`linked_prs`; re-index on status change; cross-links to Phase 2 PR chunks
(`specs/roadmap.md`). The ingest pipeline (chunk dicts → TEI embed →
Qdrant upsert → Postgres write), ACL mapping hook (Phase 7
`resource_acl_mappings`), and connector CLI pattern
(`python -m teamrag.ingest <source>`) already exist.

Tracker choice: **Jira Cloud** (REST API v3, email + API token — same
Atlassian credential pattern as the existing Confluence connector).
Linear is out of scope.

## Scope (this feature)

- **Connector** `src/teamrag/ingest/jira.py`:
  - `JiraClient` (async httpx, basic auth email+token): search issues by
    JQL over configured project keys (`project in (...) ORDER BY updated
    DESC`, capped by `JIRA_MAX_ISSUES`), fetch all comments per issue.
  - Pure document assembly: **one chunk per ticket** — title, description,
    comments (author + body), resolution line ("Resolution: Fixed" /
    "Unresolved"). Jira Cloud v3 bodies are ADF (Atlassian Document
    Format) JSON; a pure recursive `adf_to_text` extracts plain text.
  - Chunk metadata: `issue_key`, `project_key`, `status`, `assignee`,
    `reporter`, `labels`, `epic`, `resolution`, `linked_prs` (GitHub PR
    URLs regex-extracted from description+comments — the Phase 2
    cross-link), `last_updated`. Citation `source_url` =
    `{JIRA_URL}/browse/{issue_key}`. Stable chunk id =
    sha256(`jira:{issue_key}:0`).
- **Settings** (config.py + .env.example): `JIRA_URL`, `JIRA_EMAIL`,
  `JIRA_API_TOKEN`, `JIRA_PROJECT_KEYS` (comma-separated),
  `JIRA_MAX_ISSUES` (default 200), `JIRA_POLL_INTERVAL_SECONDS`
  (default 300).
- **CLI runner**: `python -m teamrag.ingest jira [--poll]` following the
  existing runner pattern (env validation, Qdrant collection ensure,
  ACL-mapping load + apply, embed, upsert, Postgres write via the
  existing single-chunk source writer). `--poll` re-runs on the interval;
  each run re-fetches by `updated` recency and upserts idempotently —
  this is the roadmap's "re-index on status change" mechanism (a status
  change bumps `updated`, the next poll re-ingests the ticket with fresh
  status/resolution metadata under the same stable chunk id).
- **ACL integration**: source type `"jira"`, resource key = `project_key`
  in `resource_acl_mappings`; Keycloak sync gains the `projects` group
  attribute → `jira` mapping.
- **Qdrant payload**: new optional keys (`issue_key`, `project_key`,
  `status`, `assignee`, `reporter`, `labels`, `epic`, `resolution`,
  `linked_prs`) added to `OPTIONAL_QDRANT_PAYLOAD_KEYS`.

## Explicitly out of scope (deferred)

- Linear connector.
- Jira webhooks / Events API push (polling only, like all connectors here).
- Rendering cross-links in answers (metadata only; UI later).
- Jira Server/Data Center API shapes (Cloud v3 only).

## Decisions

1. **One chunk per ticket** (roadmap letter) — tickets are small relative
   to BGE-M3's context; no sub-chunking.
2. **Polling = re-index trigger.** No webhook infrastructure; `updated`-
   ordered JQL + idempotent upsert converges on status changes.
3. **Reuse the generic single-chunk Postgres writer**
   (`write_chat_thread_to_postgres`) with `source_type="jira"` rather
   than adding a near-duplicate writer.
4. **linked_prs = regex-extracted GitHub PR URLs** from text. Jira remote
   links API is a nice-to-have later; text extraction covers the common
   "PR pasted in comment" case that Phase 2 indexed.

## Dependencies / assumptions

- Phases 0–7 healthy. No new Python dependencies (httpx already present).
- Live Jira credentials are NOT assumed: unit tests run on committed ADF/
  issue fixtures; the integration test seeds Qdrant directly (Phase 6
  pattern) and skips gracefully; live ingest is operator-run.

## Acceptance (roadmap "Done when")

A query like "have we tried rate-limiting at the gateway before?" against
a seeded resolved-ticket chunk returns that chunk with `status`,
`resolution`, and a `/browse/` citation URL — proven by integration test;
unit tests prove assembly (title+description+comments+resolution), ADF
extraction, metadata fields, linked-PR extraction, and stable ids.
