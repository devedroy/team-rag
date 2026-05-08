# TeamRag Tech Stack

---

## Principles

- **Retrieval is self-hosted.** All vectors, chunks, and metadata stay on internal infrastructure. The LLM that synthesizes answers may be external (Claude API) or internal (vLLM) — that decision is pluggable and does not affect the retrieval layer.
- **MCP-first interfaces.** The primary programmatic interface is an MCP server. The chat UI and any future integrations are consumers of the same retrieval API.
- **Swap without rewrite.** LLM backend, embedding model, and data sources are injected via configuration. Changing them requires no structural code changes.

---

## Components

### Retrieval

| Component | Choice | Notes |
|---|---|---|
| Vector DB | **Qdrant** | Self-hosted, strong filtering, native hybrid search, ACL filter DSL |
| Text embeddings | **BGE-M3** | Self-hosted via Text Embeddings Inference (TEI), multilingual, strong on technical content |
| Code embeddings | **Voyage-code-3** (API) or **Jina Code v2** (self-hosted) | Used for AST-chunked code only |
| Reranker | **BGE-reranker-v2-m3** | Self-hosted, applied after vector retrieval for cross-source quality |

### Ingestion

| Component | Choice | Notes |
|---|---|---|
| Orchestration | **LlamaIndex** | Source connectors, chunking strategies, pipeline composition |
| Workflow scheduler | **Temporal** or **Prefect** | Retries, backfills, webhook-triggered re-indexing |
| Code chunking | **tree-sitter** | AST-aware — function/class level chunks, not naive token splits |
| Semantic chunking | Heading-aware splitter | Respects document structure for Confluence/Notion/Drive |

### API & MCP Layer

| Component | Choice | Notes |
|---|---|---|
| API framework | **FastAPI** | Core retrieval gateway; all interfaces talk to this |
| MCP server | **MCP Python SDK** | Wraps retrieval tools, exposes to Claude Code, Cursor, Continue.dev |
| MCP transport | **stdio** (local) + **SSE/HTTP** (remote) | stdio for local IDE use; HTTP SSE for remote/team-wide deployment |

### LLM (Chat UI synthesis)

**Pluggable.** The retrieval layer is LLM-agnostic. Chat UI synthesis is configured per deployment:

| Option | When to use |
|---|---|
| Claude API (Anthropic) | Default recommendation — fast to ship, no GPU required |
| vLLM + Llama 3.3 70B | Full data control, on-prem GPU available |
| Any OpenAI-compatible endpoint | Drop-in for other providers |

The retrieval API returns chunks + metadata. The LLM is called after retrieval with the chunks as context. Swapping the LLM requires changing one config value.

### Human Chat UI

| Component | Choice | Notes |
|---|---|---|
| Chat frontend | **Open WebUI** or **LibreChat** | Both self-hostable, multi-user, SSO-ready, support custom backends |
| Slack bot | Custom **Bolt** app | Calls the FastAPI retrieval gateway, returns citations |

### Auth & Access Control

| Component | Choice | Notes |
|---|---|---|
| Identity provider | **Keycloak** or existing SSO (Okta / Google Workspace) | Group membership drives ACL filtering |
| ACL store | **Postgres** | Source of truth for chunk tags, user groups, audit log |
| Enforcement point | **Retrieval time** (not ingestion, not LLM) | Every query filters by `acl_tags ∩ user_groups` before chunks reach the LLM |

### Infrastructure

| Component | Choice |
|---|---|
| Metadata + audit DB | Postgres |
| Embedding server | TEI (Text Embeddings Inference) or Infinity |
| Container runtime | Docker / Docker Compose (dev), Kubernetes (prod) |

---

## Access tiers

```
Tier 0 — open to all engineers       (~70%): public channels, public docs, public repos
Tier 1 — squad-gated                 (~25%): private channels, squad repos, squad tickets
Tier 2 — restricted (explicit list)   (~5%): security incidents, comp, PII, exec channels
```

ACL tags are attached at ingestion. Filtered at retrieval. The LLM never sees content the user cannot see.

---

## What is explicitly out of scope

- Email (phase 2+, signal-to-noise ratio too low for v1)
- Code completion (TeamRag retrieves context; the IDE LLM completes)
- Authoring or editing source documents
