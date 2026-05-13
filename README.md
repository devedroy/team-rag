# TeamRag

A self-hosted, multi-squad RAG (Retrieval-Augmented Generation) knowledge layer for software engineering teams. Queryable by engineers through a chat UI and by AI coding assistants (Claude, Cursor, any MCP-compatible tool) through a standard MCP server.

---

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (v24+)
- [Docker Compose](https://docs.docker.com/compose/install/) (v2, included with Docker Desktop)

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` (the defaults in `.env.example` work for local development). Leave `DATABASE_URL` consistent with those credentials.

### 2. Start the stack

```bash
docker compose up -d
```

This starts three services: **postgres** (5432), **qdrant** (6333/6334), and **tei** (8080).

> Note: The `tei` service downloads BGE-M3 (~570 MB) on first run. It may take 2–3 minutes before its healthcheck passes.

### 3. Run database migrations

```bash
DATABASE_URL=postgresql+asyncpg://teamrag:teamrag@localhost:5432/teamrag alembic upgrade head
```

### 4. Verify all services are healthy

```bash
docker compose ps
```

All three services should show `healthy` in the Status column before running any application code.

### 5. Stop the stack

```bash
docker compose down
```

Data volumes (`postgres_data`, `qdrant_data`, `tei_cache`) are preserved across restarts. To remove them as well:

```bash
docker compose down -v
```

---

## MCP server (Phase 4)

The MCP layer is a thin adapter over the FastAPI gateway: tools call `POST /query` and `POST /document` (same payloads as the HTTP API). Configure the gateway URL for the MCP process with **`TEAMRAG_GATEWAY_URL`** (default `http://localhost:8000`). Optional defaults: **`TEAMRAG_QUERY_TOP_K_DEFAULT`**, and for HTTP SSE **`MCP_SSE_HOST`** / **`MCP_SSE_PORT`** (`127.0.0.1`, `8765`).

**stdio (local Cursor / Claude Code):** from the repository root, after `uv sync` and with the gateway running:

```bash
uv run python -m teamrag.mcp_server --transport stdio
```

Same entrypoint via the **`teamrag-mcp`** script (`teamrag-mcp --transport stdio`). Example Cursor / Claude Desktop–style configs: [`examples/mcp/mcp.json`](examples/mcp/mcp.json), [`examples/mcp/claude_desktop_config.json`](examples/mcp/claude_desktop_config.json) — adjust `command`/`args` if you do not use `uv`.

**HTTP SSE (team / remote):** start the MCP server with `--transport sse`, then connect your MCP client to the bound host and port (SSE path follows the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) defaults; set `MCP_SSE_HOST` / `MCP_SSE_PORT` as needed).

**Merge / manual validation:** [`specs/2026-05-13-phase-4-mcp-server/validation.md`](specs/2026-05-13-phase-4-mcp-server/validation.md).

---

## Table of Contents

1. [Goals & Context](#goals--context)
2. [What This System Unlocks](#what-this-system-unlocks)
3. [High-Value Queries](#high-value-queries)
4. [Recommended Tech Stack](#recommended-tech-stack)
5. [Tiered Access Control Design](#tiered-access-control-design)
6. [Source-by-Source Ingestion Plan](#source-by-source-ingestion-plan)
7. [Phased Rollout Plan](#phased-rollout-plan)
8. [Evaluation Framework](#evaluation-framework)
9. [Critical Design Choices](#critical-design-choices)
10. [Gotchas & Hard Parts](#gotchas--hard-parts)
11. [Specs](#specs)
12. [Next Steps](#next-steps)

---

## Goals & Context

**Team profile**
- Domain: Software Engineering
- Size: 15–50 users (multiple squads)
- Hosting: Self-hosted (data must stay internal)
- Interfaces: MCP server (Claude Code, Cursor, any MCP-compatible IDE), Chat UI, Slack bot, internal API
- Access model: Tiered (sensitive sources gated, rest open)

**Priorities (ranked)**
1. Preserving tribal knowledge
2. Faster decision-making
3. Reducing repeat questions
4. Cross-team context sharing
5. Onboarding new members

**Knowledge sources to ingest**
- Slack/Teams chats
- Docs (Confluence/Notion/Drive)
- Code & PRs (GitHub/GitLab)
- Tickets (Jira/Linear)
- Meeting notes/transcripts
- Emails (deferred to phase 2 — see gotchas)

---

## What This System Unlocks

- **Onboarding accelerator** — New hires ask "how does our auth flow work?" or "why did we pick Postgres over Mongo?" and get answers grounded in your actual docs, PRs, and past discussions instead of pinging seniors.
- **Institutional memory** — Captures the *why* behind decisions (architecture choices, customer commitments, abandoned experiments). When the person who made the call leaves, the reasoning stays.
- **Repeat-question killer** — "How do I deploy to staging?", "Who owns the billing service?", "What's our SLA for incident response?" — answered instantly from your team's own knowledge.
- **Decision support** — "Have we tried X before?" surfaces past attempts, outcomes, and post-mortems so you don't repeat mistakes.
- **Cross-functional context** — Engineers can query product specs, PMs can query technical constraints, support can query engineering changelogs — without bothering each other.
- **Meeting & standup intelligence** — "What did we commit to in last week's planning?" or "Summarize all discussions about the checkout redesign across Slack and docs."
- **Ticket triage & duplicate detection** — New bug reports auto-matched against historical tickets and resolutions.
- **Code archaeology** — "Why does this function exist?" pulls relevant PRs, design docs, and Slack threads that explain the context.
- **Compliance & audit trail** — Searchable record of who decided what, when, and based on what evidence.

---

## High-Value Queries

### Tribal knowledge (priority #1)
- "Why did we choose Kafka over RabbitMQ?" → pulls the design doc + Slack debate + PR discussion
- "Who originally built the payments service and what were the constraints?"
- "What workarounds exist for the legacy auth system?"
- "What did we learn from the 2024 outage?" → post-mortems + retro notes

### Faster decisions (priority #2)
- "Have we tried using Redis for this kind of caching before? What happened?"
- "What's our team's stance on monorepos vs polyrepos?"
- "Show me all past discussions about rate limiting strategies"
- "What did the architecture review conclude about the new ingestion pipeline?"

### Repeat questions (priority #3)
- "How do I get staging credentials?"
- "What's the on-call runbook for the API gateway?"
- "How do I add a new feature flag?"

### Cross-team context (priority #4)
- "What does the platform team mean by 'tier 1 service'?"
- "What APIs does the mobile team consume from us?"

### Onboarding (priority #5)
- "Give me a tour of our microservices and their owners"
- "What are the top 10 things a new backend engineer should read?"

---

## Recommended Tech Stack

> Full rationale in [`specs/tech-stack.md`](specs/tech-stack.md).

### MCP server (primary AI interface)
- **MCP Python SDK** — exposes `search_knowledge` and `get_document` tools
- **Transports:** stdio (local IDE use) + HTTP SSE (team-wide deployment)
- Works out of the box with Claude Code, Cursor, Continue.dev, and any MCP-compatible client
- The MCP server does **retrieval only** — the IDE's LLM (Claude, GPT-4, etc.) synthesizes answers

### LLM (Chat UI synthesis — pluggable)
- **Default:** Claude API (Anthropic) — no GPU required, fast to ship
- **Self-hosted option:** vLLM + Llama 3.3 70B or Qwen 2.5 72B on A100/H100
- The retrieval layer is LLM-agnostic; switching providers is a config change, not a code change

### Embeddings
- **Text:** BGE-M3 (self-hosted via TEI or Infinity) — multilingual, strong on technical content
- **Code:** Voyage-code-3 (API) or Jina Code v2 (self-hosted)

### Vector DB
- **Qdrant** — best self-hosted experience, strong filtering, hybrid search, native ACL filter DSL

### Metadata + ACL store
- **Postgres** — source of truth for permissions, chunk metadata, audit logs

### Ingestion
- **LlamaIndex** — pipeline composition and source connectors
- **Temporal** or **Prefect** — retries, backfills, webhook-triggered re-indexing
- **tree-sitter** — AST-aware chunking for code (function/class level, not file level)

### Reranker
- **BGE-reranker-v2-m3** self-hosted — significant quality lift for cross-source queries

### Human interfaces
- **Chat UI:** Open WebUI or LibreChat (self-hostable, multi-user, SSO-ready)
- **Slack bot:** custom Bolt app calling the FastAPI gateway
- **Internal API:** FastAPI service — all interfaces talk to this

### Auth & ACLs
- **Keycloak** or existing SSO (Okta/Google Workspace) for identity
- ACL filter applied at **retrieval time** — the LLM never sees content the user cannot see

---

## Tiered Access Control Design

### Tier 0 — Open to all engineers (~70% of content)
Public Slack channels, public Confluence spaces, public repos, public Jira projects, engineering all-hands notes.

### Tier 1 — Squad-gated (~25%)
Private squad channels, squad-owned repos, squad Jira projects → only members of that squad.

### Tier 2 — Restricted (~5%)
Security incidents, comp/HR discussions, M&A, customer PII, exec channels → explicit allowlist per document.

### Implementation
Every chunk gets an `acl_tags: ["tier-0"]` or `acl_tags: ["squad-payments", "tier-1"]` array. At query time, your service looks up the user's groups from Keycloak and adds a filter:

```
acl_tags ∩ user_groups ≠ ∅
```

Qdrant supports this natively in its filter DSL.

> **Critical:** Enforce ACLs at *retrieval*, not at the LLM. Never let the LLM see content the user can't see — even for "context."

---

## Source-by-Source Ingestion Plan

### Slack (highest tribal-knowledge value, hardest to do well)
- Use Slack's Conversations API + Events API for live updates
- Chunk by *thread*, not message — preserve full conversation
- Filter aggressively: skip channels with <5 members, skip bot messages, skip channels matching `#random`, `#fun-*`, `#announcements`
- Signal boost: threads with reactions, threads with >3 replies, threads where senior engineers participated
- Metadata per chunk: `channel`, `participants`, `thread_ts`, `reaction_count`, `has_code_block`

### Confluence / Notion / Drive
- Native APIs with webhooks for change events
- Semantic chunking respecting headings (not naive 512-token splits)
- Preserve page hierarchy as metadata for "which space/team owns this"
- Re-index on edit, not on schedule

### GitHub / GitLab (PRs + code)
Two separate pipelines:
- **Code:** AST-aware chunking with tree-sitter; embed function/class-level chunks
- **PRs:** chunk = PR title + description + review comments + linked issue, all together
- Metadata: `repo`, `author`, `merged_at`, `files_changed`, `linked_jira`
- This is where your "code archaeology" queries will shine

### Jira / Linear
- One chunk per ticket = title + description + all comments + resolution
- Metadata: `status`, `assignee`, `reporter`, `labels`, `epic`, `linked_prs`
- Re-index when status changes (closed tickets are gold for "have we tried X")

### Meeting notes / transcripts
- If you use Granola, Fireflies, Otter, Read.ai — they all have APIs/exports
- Chunk by topic (most tools auto-segment), preserve attendees
- Tag meeting type: standup (low value, skip), planning (medium), architecture review (high), retro (high)

### Emails (defer to phase 2)
Honest advice: skip in v1. Email-to-noise ratio is brutal and value overlaps with Slack/Docs. Revisit after v1 ships.

---

## Phased Rollout Plan

> Full detail with acceptance criteria in [`specs/roadmap.md`](specs/roadmap.md).

Each phase is a vertical feature slice — one end-to-end path from data source to queryable result, shippable and testable on its own.

| Phase | Slice | Done when |
|---|---|---|
| 0 | Core infrastructure | `POST /query` returns empty results with no errors |
| 1 | Confluence/Notion → retrieve → cite | Curl query returns a Confluence chunk with a working citation URL |
| 2 | GitHub PRs → retrieve → cite | "Why did we change X?" returns a PR chunk with a link |
| 3 | Human chat UI | Engineer asks a question, sees answer with clickable source links |
| 4 | MCP server | Claude Code calls `search_knowledge` and returns grounded answer |
| 5 | Tier-0 ACLs | Unauthenticated caller cannot retrieve private content |
| 6 | Slack → retrieve → cite | "Why did we switch to Kafka?" returns a Slack thread chunk |
| 7 | Squad-level ACLs (Tier-1) | Squad member sees private results; outsider does not |
| 8 | Jira/Linear → retrieve → cite | "Have we tried X?" returns a resolved ticket with outcome |
| 9 | GitHub code (AST) → retrieve | IDE agent gets function chunk + the PR that introduced it |
| 10 | Reranker | Retrieval recall@5 improves measurably over Phase 9 baseline |
| 11 | Meeting transcripts → retrieve | "What did we commit to in planning?" returns meeting chunk |
| 12 | Evaluation framework | CI runs golden question set weekly, alerts on regression |

---

## Evaluation Framework

Build a "golden question set" of 50–100 real team questions with known good answers. Examples:
- "Why did we deprecate the v1 API?"
- "Who owns the deploy pipeline?"
- "What's our retry policy for downstream service failures?"

Run weekly. Track:
- **Retrieval recall@5** — did we surface the right document?
- **Answer correctness** — LLM-as-judge + spot checks
- **Citation accuracy** — do citations actually support the claim?

Without this, quality silently degrades and you won't know.

Also add inline 👍/👎 buttons in every interface — feed these back into reranker training.

---

## Critical Design Choices

1. **Decision provenance is everything** — every chunk needs metadata: `decision_date`, `decision_owner`, `superseded_by`. This is what makes tribal knowledge searchable beyond keywords.

2. **Temporal awareness** — engineering knowledge decays. A 2021 Slack thread about "the new auth system" is misleading today. Tag every chunk with timestamps and weight recent content higher, or filter by date in queries.

3. **Cross-source linking** — the magic happens when a Jira ticket → links to its PR → links to the design doc → links to the Slack thread where it was debated. Store these relationships as graph edges or metadata.

4. **Stale-content detection** — flag chunks where the underlying file/ticket changed but RAG still has the old version. Re-index on webhooks, not just batch jobs.

---

## Gotchas & Hard Parts

- **Slack noise** — 80% of Slack is low-signal. Filter by channel allowlist + reactions/thread length as signal.
- **Email is mostly garbage for RAG** — newsletters, automated alerts, calendar invites pollute it heavily. Defer until v2.
- **Code RAG ≠ doc RAG** — code needs different embeddings (CodeBERT, Voyage-code-3) and AST chunking. Don't naively embed `.py` files like markdown.
- **Permissions are non-trivial** — if a Slack channel is private, the RAG must enforce that *at retrieval time*, not just at ingestion. Per-user query filtering based on actual ACLs.
- **Hallucinated citations** — even with RAG, LLMs invent sources. Always render answers with clickable citations to the original Slack/Jira/Doc URL.
- **Cost** — embedding 1M+ messages + continuous re-indexing adds up. Budget ~$200–2000/month depending on scale.
- **Cursor/VS Code integration is tricky** — Continue.dev works well, but your team will expect it to also do code completion. Be clear it's a *retrieval* tool, not Copilot.
- **Slack ACL sync is the trap** — channel membership changes constantly. Run a nightly job to refresh `channel_id → user_ids` mappings.
- **Decision metadata is manual at first** — auto-extracting "this is a decision" from Slack/docs is hard. Consider a lightweight `/decision` Slack command that explicitly tags decisions for premium indexing.
- **70B on one box for 15–50 users** — fine for chat-style usage (5–15 concurrent). If IDE integration sees heavy use, you'll need a second GPU or a smaller model for IDE traffic specifically.
- **Backfill is slow** — initial ingestion of years of Slack + Confluence can take 1–2 weeks of compute. Plan for it.
- **Staffing reality** — this is realistically 1–2 engineers for 3 months to v1. Don't try to ship it as a side project.

---

## Specs

The `specs/` directory is the project constitution:

- [`specs/mission.md`](specs/mission.md) — what TeamRag is, who it serves, and what success looks like
- [`specs/tech-stack.md`](specs/tech-stack.md) — full stack choices with rationale and pluggable LLM design
- [`specs/roadmap.md`](specs/roadmap.md) — 13 vertical feature slices with acceptance criteria

## Next Steps

Start at Phase 0: stand up Qdrant, the embedding server, Postgres, and the FastAPI skeleton. Nothing is indexed yet — just the pipes, ready to receive data.
