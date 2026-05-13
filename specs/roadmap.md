# TeamRag Roadmap

Each phase is a vertical feature slice: one end-to-end path from data source to queryable result, shippable and testable on its own. Later phases build on earlier ones but never break them.

---

## Phase 0 — Core infrastructure (no data yet)

Stand up the empty pipes.

- Qdrant running locally (Docker Compose)
- TEI embedding server running (BGE-M3)
- Postgres schema: `sources`, `chunks`, `acl_tags`, `audit_log`
- FastAPI skeleton with one working health endpoint
- `.env`-driven config for all service URLs

**Done when:** `POST /query` returns an empty result set with no errors.

---

## Phase 1 — Confluence/Notion → retrieve → cite

First real knowledge in the system.

- LlamaIndex connector for Confluence (or Notion)
- Semantic chunking respecting heading hierarchy
- Each chunk stored in Qdrant with metadata: `source`, `url`, `page_title`, `last_updated`
- `POST /query` returns top-k chunks with `source_url` citation field
- Smoke test: "How does our auth flow work?" returns a relevant chunk

**Done when:** A curl query returns a chunk from Confluence with a working citation URL.

---

## Phase 2 — GitHub PRs → retrieve → cite

Unlock decision archaeology.

- LlamaIndex GitHub connector ingesting PR title + description + review comments + linked issue
- Chunk metadata: `repo`, `author`, `merged_at`, `files_changed`, `linked_jira`
- Same `/query` endpoint surfaces PR chunks alongside doc chunks

**Done when:** "Why did we change the auth middleware?" returns a PR chunk with a link to the PR.

---

## Phase 3 — Human chat UI

Put a face on the retrieval backend.

- Open WebUI or LibreChat connected to FastAPI
- Pluggable LLM config (Claude API by default)
- Citations rendered as clickable links in chat responses
- Basic multi-user login (SSO stub or local accounts)

**Done when:** An engineer opens the UI, asks a question, and sees an answer with clickable source links.

---

## Phase 4 — MCP server

Make TeamRag available to AI coding assistants.

- MCP Python SDK server wrapping the retrieval API
- Two tools exposed:
  - `search_knowledge(query: str) → chunks[]` — semantic search over all indexed content
  - `get_document(source_url: str) → chunk[]` — fetch all chunks from a specific source
- stdio transport for local use (Claude Code, Cursor, Continue.dev)
- HTTP SSE transport for team-wide deployment
- `mcp.json` / `claude_desktop_config.json` example configs in repo

**Done when:** Claude Code in an IDE can call `search_knowledge` and return a grounded answer from team docs.

---

## Phase 5 — Tier-0 ACLs

Apply access control before any content reaches the LLM.

- Every chunk tagged `acl_tags: ["tier-0"]` at ingestion
- Query-time filter: `acl_tags ∩ user_groups ≠ ∅`
- Qdrant filter DSL wired into FastAPI
- Unauthenticated requests only see tier-0 content

**Done when:** A query from an unauthenticated caller cannot retrieve private content even if it exists in Qdrant.

---

## Phase 6 — Slack → retrieve → cite

The tribal-knowledge unlock.

- Slack Conversations API + Events API connector
- Chunk unit: full thread (not individual messages)
- Signal filter: skip channels with <5 members, skip bot messages, skip `#random` / `#fun-*`
- Signal boost: threads with reactions, threads with >3 replies
- Chunk metadata: `channel`, `participants`, `thread_ts`, `reaction_count`, `has_code_block`
- Slack thread URL as citation

**Done when:** "Why did we switch to Kafka?" returns a Slack thread chunk with a link to the original thread.

> **Implementation note (this repo):** Phase 6 is delivered as **Microsoft Teams + Webex Messaging** ingestion (no Slack connector). Scope, metadata, and validation live under `specs/2026-05-13-phase-6-teams-webex-ingest/`; operator setup is in `docs/ingest-teams-webex.md`.

---

## Phase 7 — Squad-level ACLs (Tier-1)

Gate private content to squad members.

- Keycloak (or Okta/Google Workspace) group sync
- Nightly job: refresh `channel_id → user_ids` and `repo → squad` mappings in Postgres
- Private Slack channels, squad repos tagged `acl_tags: ["squad-payments", "tier-1"]`
- Per-user group lookup injected into every query filter

**Done when:** A user in squad-payments sees private payments channel results; a user outside does not.

---

## Phase 8 — Jira/Linear → retrieve → cite

Close-ticket knowledge: "have we tried this before?"

- One chunk per ticket = title + description + all comments + resolution
- Metadata: `status`, `assignee`, `reporter`, `labels`, `epic`, `linked_prs`
- Re-index trigger on status change (closed tickets are high-value signal)
- Cross-link: chunk metadata references related PR chunks from Phase 2

**Done when:** "Have we tried rate-limiting at the gateway before?" returns a resolved Jira ticket with outcome.

---

## Phase 9 — GitHub code → retrieve → cite (code archaeology)

AST-aware code understanding.

- tree-sitter chunking: function/class level, not file-level
- Code-specific embeddings (Voyage-code-3 or Jina Code v2)
- Chunk metadata: `repo`, `file_path`, `symbol_name`, `language`
- "Why does this function exist?" query pulls relevant PRs (Phase 2) + design docs (Phase 1)

**Done when:** An IDE agent calls `search_knowledge("rate limiter implementation")` and gets the right function chunk + the PR that introduced it.

---

## Phase 10 — Reranker

Quality upgrade across all sources.

- BGE-reranker-v2-m3 deployed via TEI
- Applied after vector retrieval, before LLM context assembly
- Especially impactful for cross-source queries (doc + Slack + PR all in one result)

**Done when:** Retrieval recall@5 on the golden question set improves measurably over Phase 9 baseline.

---

## Phase 11 — Meeting transcripts → retrieve → cite

Capture what was committed to in planning and architecture reviews.

- API connector for Granola / Fireflies / Otter / Read.ai (whichever the team uses)
- Chunk by topic segment (most tools auto-segment)
- Metadata: `attendees`, `meeting_type`, `date`
- Skip standups (low value); prioritize planning, architecture review, retro

**Done when:** "What did we commit to in last week's planning?" returns a meeting chunk with attendees listed.

---

## Phase 12 — Evaluation framework

Prevent silent quality degradation.

- Golden question set: 50–100 real team questions with known good answers
- Weekly automated run measuring:
  - Retrieval recall@5 (did the right chunk surface?)
  - Answer correctness (LLM-as-judge)
  - Citation accuracy (does the citation support the claim?)
- Inline 👍 / 👎 in chat UI and Slack bot, fed back to reranker

**Done when:** CI runs the eval suite weekly and alerts on regression.

---

## Deferred

- **Email ingestion** — signal-to-noise too low; revisit after Phase 6 ships
- **Decision graph** — cross-source links as graph edges (Jira → PR → Doc → Slack thread)
- **Restricted Tier-2 ACLs** — security incidents, comp, PII; explicit allowlist per document
- **Feedback-driven reranker fine-tuning** — requires enough 👍/👎 volume (3–6 months of data)
