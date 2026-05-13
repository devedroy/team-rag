# Phase 6 — Teams + Webex ingestion — requirements

## Context

TeamRag exists to make the team's tribal knowledge "permanent, searchable, and citable" across the surfaces engineers actually talk in (`specs/mission.md`). Chat-thread context is the highest-signal source for "why did we decide X?" archaeology, and the retrieval layer is platform-agnostic by design (`specs/tech-stack.md`: LlamaIndex and related parsers for **chunking** where applicable, custom HTTP clients per source, Qdrant for vector storage with payload filtering, FastAPI as the single retrieval gateway).

The roadmap names this phase **"Slack → retrieve → cite"** (`specs/roadmap.md` Phase 6). This deployment does **not** use Slack — the team runs on **Microsoft Teams** and **Webex Messaging**. This spec therefore reframes Phase 6 as a Teams + Webex ingestion slice while preserving the roadmap's intent: full-thread chunks, chat-URL citations, signal filtering, and parity with Phase 1 / Phase 2 source coverage in `POST /query`.

## Scope (this feature)

- Two new ingestion connectors:
  - **Microsoft Teams** via Microsoft Graph (channels + threaded replies in joined teams).
  - **Webex Messaging** via the Webex REST API (spaces + threaded replies the bot is a member of).
- **Both connectors must ship in this phase**, in parallel; merging requires both producing chunks end-to-end.
- **Auth model:** delegated / bot identity for both platforms.
  - Teams: bot registered in Entra ID, added to the teams/channels we want indexed; only data the bot can see is ingested.
  - Webex: bot account / integration; only spaces the bot is a member of are ingested.
- **Channel/space selection:** ingest **all channels/spaces the bot has access to**, then apply signal filters at chunk-creation and/or query-time payload-filter level rather than gating ingestion behind an allowlist.
  - Skip messages authored by bots/apps.
  - Skip threads with fewer than a configurable minimum signal threshold (default: <5 unique participants in the parent channel/space, or threads with only the original message and no replies).
  - Boost / preserve threads with reactions or >3 replies (preserved as payload metadata; reranking is Phase 10).
- **Chunk unit:** **full thread** (root message + replies concatenated into a single chunk). One thread = one Qdrant point.
- **Chunk metadata** (Qdrant payload + Postgres `Chunk.chunk_metadata` / JSONB column `metadata`):
  - `source` ∈ {`teams`, `webex`}
  - `tenant_id` / `org_id` (Teams `tenant`, Webex `orgId`)
  - `channel_id` / `space_id`, `channel_name` / `space_title`
  - `thread_id` (Teams `messageId` of root; Webex `parentId` or root `id`)
  - `participants` (display names or stable user IDs of everyone who posted in the thread)
  - `reply_count`, `reaction_count`, `has_code_block`
  - `last_activity_at` (timezone-aware)
  - `source_url` — deep link to the thread (Teams `webUrl`, Webex space + parent message permalink)
- **HTTP / MCP response contract (Phases 1–5 baseline):** `POST /query`, `POST /document`, and the MCP tools that call them expose only the existing **`ChunkResult`** fields: `content`, `source_url`, `page_title`, `score` (see `src/teamrag/api/query.py`). Chat connectors **must** set Qdrant payload `page_title` to the human-visible **channel name** (Teams) or **space title** (Webex), matching the compatibility pattern used for GitHub (`pr_title` → `page_title` in ingest). Full chat-specific fields (`source`, `thread_id`, tenant/org ids, etc.) are required on **Postgres `chunks.chunk_metadata` and in the Qdrant payload** for persistence, tests, and ops; they are **not** part of the public JSON shape for this phase unless a later phase extends `ChunkResult` (explicitly out of scope here).
- **Citation:** `source_url` in the `/query` response must be a working deep link to the originating thread; the human-readable context line uses **`page_title`** (channel or space title), parity with Phase 1 / Phase 2 title behavior.
- **`POST /query`** continues to surface chunks from these new sources alongside doc and PR chunks via the same gateway path; **no new endpoints** are added in this phase.
- **MCP** (`search_knowledge`, `get_document`) returns the same **`ChunkResult`-shaped** chunk dicts as the gateway because it delegates to `POST /query` / `POST /document` with **identical tier-0 ACL and serialization** as unauthenticated HTTP callers (Phase 4 + 5) — verified by tests, not reimplemented in the MCP process.

## Decisions

1. **Roadmap reframe:** Phase 6 is delivered as **Teams + Webex** instead of Slack. The roadmap entry stays as-is for historical context; this spec is the authoritative scope for the implementation that satisfies the phase's intent ("the tribal-knowledge unlock").
2. **Ingestion mode:** **Backfill + periodic polling** only.
   - One-shot historical backfill per channel/space on connector enable.
   - Scheduled polling job picks up new threads and new replies to existing threads.
   - **No** Microsoft Graph change-notification subscriptions, **no** Webex webhooks in this phase. Real-time push is deferred.
3. **Both connectors ship together** in one phase (not sequenced into two sub-merges). A single merge is gated on Teams and Webex both producing a citable thread chunk via `POST /query`.
4. **Channel discovery:** ingest **everything the bot has access to**; do not maintain an allowlist/denylist in this phase. Privacy is mediated by *what the bot is invited to* on the platform side. Signal filters live in the connector, not in operator config.
5. **Chunk granularity:** **one chunk per thread**, never per message. Threads update in place (re-upsert by stable `thread_id`) when new replies arrive in polling cycles.
6. **ACL tagging:** **deferred to Phase 7 (Squad-level ACLs).**
   - Phase 6 emits chunks through the existing ingestion path; chunks pick up whatever default `acl_tags` Phase 5 attaches (expected: `tier-0`).
   - Phase 6 does **not** introduce per-channel privacy detection, per-space ACL mapping, or any "private Teams channel" handling. Phase 7 will retroactively re-tag chunks once squad / channel-membership sync exists.
   - Operators are expected to keep the bot out of channels/spaces whose contents must not be tier-0 until Phase 7 lands. This constraint is documented in the connector README.
7. **Embedding & retrieval stack unchanged:** BGE-M3 via TEI for embeddings, Qdrant for vector + payload filter (`specs/tech-stack.md`). No new vector DB, no new embedding model, no reranker in this phase (reranker is Phase 10).
8. **Orchestration:** LlamaIndex connectors where one exists upstream; otherwise a thin custom reader feeding the same chunking pipeline used by Phase 1/2. Scheduler choice (Temporal vs Prefect vs cron-in-container) may be deferred — a simple periodic async task inside the gateway process is acceptable for the polling loop if no scheduler is in the repo yet.

## Explicitly out of scope (deferred)

- **Real-time push ingestion** (Graph change notifications, Webex webhooks). Tracked for a follow-up phase.
- **Private-channel ACL logic / squad tagging** for Teams or Webex — explicit Phase 7 work.
- **Direct messages and group DMs** — Phase 6 indexes channels/spaces only.
- **Attachments, file uploads, recordings** linked into messages — text body only; attachment indexing is out of scope.
- **Reactions as ranking signal** at retrieval time — stored as metadata only; reranker is Phase 10.
- **Slack connector** — explicitly not built; if the team adopts Slack later, it becomes a new spec.
- **Cross-thread / cross-platform decision linking** (Teams thread ↔ Jira ↔ PR) — Phase 8+ concern.

## Dependencies / assumptions

- Phase 0 infra (Postgres, Qdrant, TEI) and Phase 5 default tagging behavior are in place; new chunks inherit the Phase 5 default `acl_tags`.
- **Phase 4 MCP server** is in place: `teamrag-mcp` tools call the gateway via `TeamRagGateway` (`src/teamrag/mcp_server/gateway_client.py`); validation reuses the same patterns as `tests/integration/test_mcp_gateway_acl.py` and `tests/unit/test_mcp_handlers.py`.
- A bot identity is provisioned in Entra ID for Teams and as a Webex bot/integration before connector enablement; credentials arrive via `.env` (additions documented in `.env.example`).
- Existing ingestion patterns for Phase 1 (Confluence/Notion) and Phase 2 (GitHub PRs) are the structural template for the new connectors — chunk model, metadata persistence, and Qdrant upsert path are reused, not re-invented.
- MCP server continues to delegate retrieval to the FastAPI gateway (no MCP-specific ingestion or Qdrant access).

## Open questions (track in implementation PRs, non-blocking for spec)

- Polling cadence default (e.g., 5 min vs 15 min) and per-platform rate-limit handling.
- Exact `participants` representation: Entra `oid` / Webex `personId` vs display name (privacy + stability trade-off).
- Whether Teams "channel messages" and "chat messages" are both in scope or channels only (this spec assumes **channels only** for Phase 6).
- Whether to store the raw message HTML/markdown for citation preview or only normalized text.
