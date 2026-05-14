# Phase 6 — Teams + Webex ingestion — plan

**Prerequisites:** Phase 0 infrastructure, Phase 4 MCP server (`teamrag-mcp`, handlers → `TeamRagGateway` → `POST /query` / `POST /document`), and Phase 5 tier-0 tagging + retrieval filters.

Numbered groups are **sequenced**; within a group, tasks may proceed in parallel where safe. Teams and Webex connectors are built in parallel within each group unless noted.

## 1. Foundations: config, models, shared chunking (first)

1.1. Add config surface for both platforms in `src/teamrag/config.py` and `.env.example`:
- `TEAMS_TENANT_ID`, `TEAMS_CLIENT_ID`, `TEAMS_CLIENT_SECRET` (or bot framework equivalents), `TEAMS_BOT_USER_ID`
- `WEBEX_BOT_TOKEN`, `WEBEX_ORG_ID`
- `CHAT_INGEST_POLL_INTERVAL_SECONDS` (default e.g. 300)
- `CHAT_INGEST_MIN_PARTICIPANTS` (default 5) and `CHAT_INGEST_SKIP_NO_REPLIES` (default true)

1.2. Define a shared **chat-thread chunk schema** (Pydantic + SQLAlchemy metadata payload) covering both platforms: `source`, `tenant_id|org_id`, `channel_id|space_id`, `channel_name|space_title`, `thread_id`, `participants`, `reply_count`, `reaction_count`, `has_code_block`, `last_activity_at`, `source_url`. Reuse the existing `Source` / `Chunk` models — this is metadata payload shape, not a new table.

1.3. Confirm `sources` table entries for `teams` and `webex` source types (add seed rows or upsert-on-startup, mirroring the pattern used by Phase 1/2 connectors).

1.4. Add (or extend) a shared **thread → chunk text** normalizer: concatenates root + replies in chronological order, strips Teams HTML to plain text, strips Webex markdown control chars, detects code blocks for `has_code_block`, and yields a single string per thread. Unit-tested in isolation. (Full-thread chunks may bypass LlamaIndex splitters; they still feed the same TEI embed + `upsert_to_qdrant` + Postgres writers as Phase 1/2.)

1.5. Extend `src/teamrag/ingest/pipeline.py` `upsert_to_qdrant` so chat-specific payload keys (`source`, `thread_id`, tenant/org identifiers, etc.) are persisted to Qdrant alongside the existing optional-key allowlist (`space_key`, `page_id`, `pr_number`, …), or refactor that allowlist into a small shared helper so metadata is never silently dropped at upsert time.

## 2. Teams connector (Microsoft Graph)

2.1. Implement `src/teamrag/ingest/teams.py`:
- Auth via the bot's delegated/app token (Entra ID).
- Enumerate `me/joinedTeams` → `team/channels` for each joined team (no allowlist).
- For each channel: list messages, then for each root message list replies (Graph `messages/{id}/replies`).
- Build thread objects = root + replies, normalize via 1.4, build chunk metadata per 1.2.
- Embed via TEI and upsert to Qdrant; persist `Chunk` row in Postgres. Key the Qdrant point ID off a deterministic hash of `(source='teams', tenant_id, channel_id, thread_id)` so re-polls upsert (not duplicate).

2.2. Apply signal filters at chunk creation:
- Skip messages with `from.application` (bot/app authored).
- Skip threads where the channel has < `CHAT_INGEST_MIN_PARTICIPANTS` distinct human authors *over the visible history window*.
- Skip threads with zero replies if `CHAT_INGEST_SKIP_NO_REPLIES` is true.

2.3. Build `source_url` as the Graph `webUrl` of the root message (deep link into Teams desktop/web).

2.4. Polling loop: wire Teams the same way as Phase 1/2 today — extend `python -m teamrag.ingest` in `src/teamrag/ingest/__main__.py` with a `teams` source (e.g. `python -m teamrag.ingest teams`), unless this phase introduces a separate scheduler process (Decision 8 in `requirements.md` also allows a periodic async task from FastAPI lifespan). The loop walks channels and re-upserts threads whose `lastModifiedDateTime` is newer than the stored chunk's `last_activity_at`.

2.5. Unit tests for the normalizer, filter predicates, and metadata mapping. Integration test that, against a recorded/fixture payload, produces the expected chunk row and Qdrant payload.

## 3. Webex connector (in parallel with group 2)

3.1. Implement `src/teamrag/ingest/webex.py`:
- Auth via `WEBEX_BOT_TOKEN`.
- Enumerate `rooms` (`type=group`) the bot is a member of (no allowlist).
- For each room: list messages, then group by `parentId` to reconstruct threads (root = message with no `parentId`; replies = messages with `parentId == root.id`).
- Build thread objects, normalize via 1.4, build chunk metadata per 1.2.
- Embed + upsert as in 2.1 with deterministic ID over `(source='webex', org_id, space_id, thread_id)`.

3.2. Apply the same signal filters as 2.2, adapted to Webex fields (`personType` to detect bots; `roomId` membership count via `memberships` API).

3.3. Build `source_url` as the Webex deep link for the root message (space URL + message id form, per Webex API docs).

3.4. Polling loop: add `python -m teamrag.ingest webex` alongside Teams in `__main__.py` (shared cadence config), or share the same lifespan task if that entrypoint is chosen instead of the CLI.

3.5. Unit + fixture-based integration tests mirroring 2.5.

## 4. Retrieval surface (after both connectors produce chunks)

4.1. **No changes** to `POST /query` or `POST /document` **request** shapes. **Response** shape stays the existing `ChunkResult` model in `src/teamrag/api/query.py` (`content`, `source_url`, `page_title`, `score` only). New chat chunks flow through `src/teamrag/retrieval.py` the same way as Confluence/GitHub: human-visible channel/space title is carried on the Qdrant payload as **`page_title`** (same pattern as GitHub `pr_title` → `page_title`).

4.2. Ensure chat ingest sets `page_title` on each chunk dict before `upsert_to_qdrant` / Postgres writes so `/query` and MCP callers see the channel or space title without extending `ChunkResult`. Full `source`, `thread_id`, and related fields live in Postgres `chunk_metadata` and the Qdrant payload per §1.2 / §1.5; `ChunkHit` in `retrieval.py` still carries the full payload internally, but the HTTP layer does not expose arbitrary payload keys today.

4.3. Add an integration test that seeds Teams and Webex thread points, issues `POST /query`, and asserts each hit has a valid `source_url` and `page_title` matching the seed; assert `source` and thread identifiers on the **stored** Postgres row and/or Qdrant payload (not in the JSON body of `/query`).

4.4. Verify MCP `search_knowledge` and `get_document` in `src/teamrag/mcp_server/handlers.py` return the **same tier-0–filtered, `ChunkResult`-shaped** chunk dicts as `POST /query` / `POST /document` (via `TeamRagGateway` — no MCP-side Qdrant). Add an MCP-level integration test analogous to Phase 5's MCP coverage (`tests/integration/test_mcp_gateway_acl.py`, `tests/unit/test_mcp_handlers.py` patterns).

## 5. Operations, docs, and ACL handoff

5.1. Document bot provisioning steps in a new `docs/ingest-teams-webex.md` (or extend README): Entra app registration + Teams bot install, Webex bot creation, required scopes, how to add the bot to a channel/space.

5.2. Add a warning in the same doc and inline in connector module docstrings: **until Phase 7 ships, do not invite the bot to channels/spaces whose contents must not be tier-0 visible**, since ACL tagging is deferred.

5.3. Confirm new chunks pick up the Phase 5 default `acl_tags` (expected: `tier-0`). Add an assertion to the ingestion integration test that the persisted chunk row carries the Phase 5 default tag set.

5.4. Update `specs/roadmap.md` Phase 6 entry with a brief note pointing at this spec directory ("delivered as Teams + Webex; see `specs/2026-05-13-phase-6-teams-webex-ingest/`"). Do not rewrite the roadmap entry.

5.5. Update `.env.example` with all new variables introduced in 1.1, and add a one-line description for each.

## 6. Final verification gate

6.1. Full automated test suite green: `uv run pytest tests/ -v` with Docker stack up.

6.2. Execute the manual checklist in `validation.md` against a live tenant + Webex org with the bot installed in at least one real channel and one real space. Attach evidence (curl output, screenshots of citation links resolving) to the implementing PR.

6.3. Reviewer confirms scope matches `requirements.md` — specifically that no real-time push, no private-channel ACL logic, and no DM ingestion was added.
