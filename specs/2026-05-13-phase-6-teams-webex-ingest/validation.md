# Phase 6 — Teams + Webex ingestion — validation

## Definition of done (roadmap alignment, restated for this stack)

`specs/roadmap.md` Phase 6:

> **Done when:** "Why did we switch to Kafka?" returns a Slack thread chunk with a link to the original thread.

For this deployment (no Slack, Teams + Webex instead), the equivalent merge bar is:

> A `POST /query` to the FastAPI gateway returns **at least one Teams thread chunk and at least one Webex thread chunk** for a query whose answer lives in those threads, with `source_url` populated and resolvable to the originating thread in each platform's UI, and `page_title` showing the expected channel or space title (see `requirements.md` HTTP contract).

Both platforms must demonstrate this — Teams alone or Webex alone does **not** satisfy the merge bar, per `requirements.md` decision 3.

## Automated checks (required before merge)

All of the following must pass under `uv run pytest tests/ -v` with the Docker Compose stack healthy:

- **Unit tests**
  - Thread normalizer: root + replies concatenate in chronological order; Teams HTML stripped; Webex markdown handled; `has_code_block` correctly detected.
  - Signal filters: drop bot-authored messages; drop low-participant or zero-reply threads per the configured thresholds.
  - Deterministic chunk-ID hashing for both `teams` and `webex` re-upserts (same thread → same Qdrant point ID).

- **Integration tests (per connector)**
  - Given a fixture/recorded Graph or Webex response, the connector produces:
    - The expected number of `Chunk` rows in Postgres with correct `source_url`, `participants`, `reply_count`, `reaction_count`, `has_code_block`, `last_activity_at`, and **`source` = `teams` | `webex` (and related ids) in `chunks.chunk_metadata`** — asserted against the database, not via `POST /query` JSON (which does not expose `source`).
    - One Qdrant point per thread with payload matching the Postgres metadata (including `source` and chat-specific keys on the payload).
    - Chunks carry the Phase 5 default `acl_tags` (expected: `tier-0`); no per-channel ACL logic is exercised.

- **Retrieval integration test**
  - Seed one Teams thread chunk and one Webex thread chunk into Qdrant + Postgres.
  - Issue `POST /query` with a phrase that semantically hits each seeded chunk.
  - Assert each hit uses the **Phase 1–5 chunk JSON shape**: non-empty `source_url`, non-empty **`page_title`** equal to the seeded channel/space title, `content`, and `score`.
  - Separately assert the two platforms are distinguishable (e.g. distinct `source_url` host/paths matching Teams vs Webex seeds, and/or by reading seeded Qdrant payload / Postgres `chunk_metadata` for `source`).

- **MCP integration test**
  - Through the MCP `search_knowledge` handler (using the existing MCP gateway client / ASGI harness, same patterns as Phase 5 MCP tests), confirm hits match **`POST /query`** for the same query (same `ChunkResult` fields, same tier-0 ACL behavior).
  - Confirm `get_document(source_url=...)` (gateway `POST /document`) returns the chunk(s) for that thread URL with the same field contract.

- **Polling re-upsert test**
  - Run the connector against a fixture, then a "second poll" fixture that adds a reply to one existing thread.
  - Assert the chunk count is unchanged for that thread (still one chunk), `reply_count` and `last_activity_at` are updated, and the Qdrant point ID is the same (upsert, not insert).

CI must be green on the implementing branch.

## Manual checklist (required before merge)

Record commands, screenshots, and outcomes in the PR description.

1. **Stack health:** `docker compose ps` shows Postgres, Qdrant, and TEI healthy.

2. **Bot install (Teams):** The Teams bot is installed in **at least one real channel** in the target tenant; confirm via the Teams UI that the bot appears in the channel's app list.

3. **Bot install (Webex):** The Webex bot is a member of **at least one real space**; confirm via `GET /memberships` or the Webex UI.

4. **Backfill run:** Trigger the connector entrypoint once (`python -m teamrag.ingest teams` / `webex`, or the chosen lifespan task); observe logs showing channels/spaces enumerated and threads upserted. No 4xx/5xx auth errors.

5. **Poll cycle:** Wait one `CHAT_INGEST_POLL_INTERVAL_SECONDS` cycle (or trigger a second run); post a new reply to an existing thread in Teams and in Webex; confirm the next poll updates that chunk's `reply_count` and `last_activity_at` without duplicating it.

6. **Citable query (Teams):**
   ```
   curl -X POST http://localhost:8000/query \
     -H "Content-Type: application/json" \
     -d '{"query":"<a phrase known to be discussed in a real Teams thread>","top_k":5}'
   ```
   Confirm a Teams chunk in the response (`source_url` + `page_title` for the channel) and click its `source_url` — it must open the originating Teams thread.

7. **Citable query (Webex):** Same as 6 against a phrase from a real Webex space; `source_url` must open the originating Webex space scrolled/anchored to the root message; `page_title` should match the space title.

8. **MCP smoke:** Invoke `search_knowledge` via the documented local MCP client flow (`examples/mcp/mcp.json` / `claude_desktop_config.json`) with the same phrases; confirm Teams and Webex chunks surface with working `source_url` and expected `page_title` (same shaping as `POST /query`).

9. **Filter sanity:** Confirm that a bot-authored message and a single-message no-reply thread in the indexed channels did **not** become chunks (inspect Postgres `chunk_metadata` / Qdrant payload for `source` + recent `created_at`, or equivalent queries).

10. **ACL deferral check:** Confirm that all Phase 6 chunks created during this validation carry `acl_tags = ['tier-0']` (or whatever Phase 5 currently emits as the default). Confirm **no** new code paths attach platform-specific privacy tags — that is Phase 7 work.

## Merge approval

- Implementing engineer self-certifies automated + manual sections above and attaches evidence (curl outputs, screenshots of resolved citation links for Teams and Webex) to the PR.
- Reviewer confirms:
  - Scope matches `requirements.md` — Teams **and** Webex both shipping, backfill + polling only, full-thread chunks, ACL deferred.
  - No real-time push (Graph subscriptions / Webex webhooks) was added.
  - No DMs, attachments, or private-channel ACL logic was added.
  - `.env.example`, connector docs, and the roadmap pointer note are updated.
