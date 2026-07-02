# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TeamRag** is a self-hosted RAG (Retrieval-Augmented Generation) system for engineering teams. It provides two interfaces:
1. **MCP server** — for AI coding assistants (Claude Code, Cursor, Continue.dev)
2. **Chat UI** — for engineers asking questions (Open WebUI or LibreChat)

The retrieval layer is LLM-agnostic: the backend does semantic search and returns ranked chunks; the frontend LLM synthesizes answers and cites sources.

**Current phase:** Phase 0 (core infrastructure). The system stands up empty pipes (Qdrant, Postgres, FastAPI) and validates all services work together. Actual ingestion connectors come in later phases.

**Mission:** See `specs/mission.md`. **Tech stack rationale:** See `specs/tech-stack.md`. **Roadmap:** See `specs/roadmap.md`.

---

## Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────┐
│                    External Interfaces                      │
│  IDE (Claude Code / Cursor)    │    Chat UI (Open WebUI)     │
└────────────────┬───────────────────────┬────────────────────┘
                 │                       │
          ┌──────┴───────────────────────┴──────┐
          │      FastAPI Gateway (8000)         │
          │  - GET  /health                     │
          │  - POST /query (retrieval only)     │
          └──────┬───────────────────────┬──────┘
                 │                       │
    ┌────────────┴──────┐        ┌───────┴─────────┐
    │   Postgres 5432   │        │  Qdrant 6333    │
    │  ├─ sources       │        │  ├─ embeddings  │
    │  ├─ chunks        │        │  └─ vector      │
    │  ├─ acl_tags      │        │     search      │
    │  └─ audit_log     │        └─────────────────┘
    └────────────────────┘                │
                                    ┌─────┴──────────┐
                                    │   TEI 8080     │
                                    │  (BGE-M3 model)│
                                    └────────────────┘
```

**Data flow for a query (Phase 0 stub)**
1. Frontend sends `POST /query {"query": "...", "top_k": 5}`
2. FastAPI gateway receives it (validation via Pydantic)
3. For Phase 0, returns empty chunk list unconditionally
4. Later phases will do: normalize query → embed (TEI) → search Qdrant → filter by ACL (Postgres) → return ranked chunks

**Key architectural principle:** ACLs are enforced at *retrieval time*, never before. The LLM should never see content the user can't see, even for "context."

---

## Tech Stack

- **FastAPI** (0.111.0+) — async HTTP gateway; all endpoints are async
- **SQLAlchemy 2.x + asyncpg** — async ORM for Postgres
- **Alembic** — version-controlled schema migrations
- **Pydantic** — request/response validation via `BaseModel`
- **Qdrant** (v1.13.1) — vector database; native ACL filter DSL
- **TEI** (Text Embeddings Inference) — CPU-friendly embedding server running BGE-M3 (1024-dim vectors)
- **Postgres 16** — metadata, audit logs, ACL storage
- **pytest + pytest-asyncio** — integration tests

All services run in Docker Compose. Database uses async driver (`asyncpg`); all app code is async-first.

---

## Using uv

This project uses **uv** as the package manager — a fast, reliable Python package manager and task runner.

- **First time?** Run `uv sync` to create `.venv` and install all dependencies from `pyproject.toml`.
- **Dependencies outdated?** Run `uv sync` after editing `pyproject.toml`.
- **Run anything?** Prefix Python commands with `uv run` (e.g., `uv run pytest`, `uv run python script.py`).
- **Lock file?** `uv.lock` is auto-generated; commit it to ensure reproducible installs.

Python version is pinned to 3.11+ via `.python-version`. uv will automatically use this version when you run commands.

---

## Project Structure

```
src/teamrag/
├── main.py              # FastAPI app entry point; lifespan handlers
├── config.py            # Pydantic Settings; env var injection
├── api/
│   ├── health.py        # GET /health (200 + {"status": "ok"})
│   └── query.py         # POST /query (Phase 0: returns empty chunks)
└── db/
    ├── models.py        # SQLAlchemy ORM: Source, Chunk, AclTag, AuditLog
    └── session.py       # Async session factory (if added in later phases)

tests/
├── integration/
│   └── test_phase0.py   # Tests /health, /query, Qdrant collection, Postgres schema
└── conftest.py          # pytest fixtures; sets DATABASE_URL default

alembic/
├── versions/
│   └── d05fmgudxt94_initial_schema.py   # Phase 0 schema: 4 tables
└── env.py               # Alembic config (autogenerate, batch mode for SQLite compat)

specs/                    # Project constitution (do not edit without understanding roadmap)
├── mission.md           # What TeamRag is and who it serves
├── tech-stack.md        # Stack choices with rationale
└── roadmap.md           # 13 phases: infrastructure → Confluence → GitHub → ...

scripts/
└── smoke_test.sh        # Minimal curl-based smoke test (no Python required)

docker-compose.yml       # postgres, qdrant, tei services
.env.example             # All required environment variables (committed to repo)
pyproject.toml           # setuptools build config; dependencies
```

---

## Common Commands

### Setup

```bash
# 1. Copy environment file (edit if needed for local credentials)
cp .env.example .env

# 2. Install dependencies with uv (creates .venv)
uv sync

# 3. Start Docker services (Postgres, Qdrant, TEI)
docker compose up -d

# 4. Run migrations
uv run alembic upgrade head

# 5. Verify all services are healthy (should show ✓ for postgres, qdrant, tei)
docker compose ps
```

### Development

```bash
# Run the FastAPI app (8000) with auto-reload
uv run fastapi run src/teamrag/main.py

# Test an endpoint
curl http://localhost:8000/health
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query":"test","top_k":5}'
```

### Testing

```bash
# Run all integration tests (requires docker-compose stack running)
uv run pytest tests/ -v

# Run a single test
uv run pytest tests/integration/test_phase0.py::test_health_returns_200 -v

# Run with coverage
uv run pytest --cov=src tests/ -v
```

### Migrations

```bash
# Create a new migration (auto-detects schema changes)
uv run alembic revision --autogenerate -m "your migration name"

# Apply all pending migrations
uv run alembic upgrade head

# Revert one migration
uv run alembic downgrade -1

# See current migration state
uv run alembic current

# See migration history
uv run alembic history
```

### Docker

```bash
# View logs from all services
docker compose logs -f

# View logs from one service
docker compose logs -f postgres

# Restart services (preserves data)
docker compose restart

# Destroy everything including data volumes
docker compose down -v
```

### Validation

```bash
# Smoke test (requires app running on :8000)
bash scripts/smoke_test.sh

# Or set custom URL
TEAMRAG_URL=http://localhost:9000 bash scripts/smoke_test.sh

# Quick Python import check
uv run python -c "from teamrag.config import settings; print(settings)"
```

---

## Key Patterns & Conventions

### Async-First

All I/O is async. DB queries, HTTP calls, and Qdrant client operations use `async`/`await`. Never use blocking calls in API handlers.

```python
# ✓ correct
async def query(request: QueryRequest) -> QueryResponse:
    async with AsyncQdrantClient(...) as client:
        results = await client.search(...)
    return QueryResponse(...)

# ✗ wrong
def query(request: QueryRequest) -> QueryResponse:
    client = QdrantClient(...)  # blocking
    results = client.search(...)
```

### Request/Response Models

All API contracts use Pydantic `BaseModel`. Never return raw dicts; always use a response model.

```python
# In api/query.py
class QueryRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=100)  # validate bounds

class QueryResponse(BaseModel):
    chunks: list[dict] = []
    total: int = 0

@router.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    ...
```

### Environment Variables

All configuration (URLs, credentials, port numbers) comes from `.env` via Pydantic `SettingsConfigDict`. No hardcoded values in source.

```python
# In config.py
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    QDRANT_URL: str = "http://localhost:6333"
    POSTGRES_PASSWORD: str  # no default; must be in .env

settings = Settings()  # reads from .env at startup
```

Add new settings to both `config.py` and `.env.example`.

### ORM Models

SQLAlchemy 2.x with mapped types. PK is always UUID (generated by Postgres `gen_random_uuid()`). All timestamps are timezone-aware. Relationships use cascade rules.

```python
class Chunk(Base):
    __tablename__ = "chunks"
    
    id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    content: Mapped[str]
    chunk_metadata: Mapped[Optional[dict]] = mapped_column(
        "metadata", postgresql.JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
    )
```

### Logging

Use Python's standard `logging` module. Each module has a logger:

```python
import logging
logger = logging.getLogger(__name__)

logger.info("Qdrant is reachable at %s", settings.QDRANT_URL)
logger.warning("Service X is not reachable: %s", exc)
```

FastAPI logs go to stderr by default. For debugging, see `docker compose logs -f`.

---

## Important Context

### Phase Sequencing

Each phase is a vertical feature slice: ingest source → index → retrieve → cite. Phase 0 (current) has no ingestion; it just validates the infrastructure works. Phases 1–12 add data sources and features in order (see `specs/roadmap.md`).

**Phase 0 is a prerequisite for all future work.** Every phase assumes Qdrant, Postgres, and the FastAPI gateway are running and healthy.

### Qdrant Collection Bootstrap

In Phase 0, the collection doesn't exist yet. The test `test_qdrant_collection_exists()` auto-creates it if missing (1024-dim COSINE distance). This is a one-time bootstrap; later phases assume it exists.

```python
# In test_phase0.py
try:
    collection_info = await client.get_collection("teamrag")
except UnexpectedResponse:
    # Create minimal collection on first run
    await client.create_collection(
        collection_name="teamrag",
        vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
    )
```

### Database Migrations

Migrations are version-controlled in `alembic/versions/`. Always use Alembic for schema changes:

1. Modify a model in `db/models.py`
2. Run `alembic revision --autogenerate -m "your message"` — creates a new file
3. Review the generated migration (ensure logic is correct)
4. Run `alembic upgrade head` to apply it
5. Commit both the model change and the migration file

Never manually run SQL DDL. Alembic tracks schema state and prevents conflicts.

### ACL Model (Phase 7 — shipped)

Four core tables form the ACL system:
- **sources** — where data came from (Confluence page, GitHub PR, Teams/Webex thread, Jira ticket)
- **chunks** — indexed text segments, each with a source_id
- **acl_tags** — many-to-many: chunk ↔ access tags (e.g., "tier-0", "squad-payments")
- **audit_log** — immutable query log (who asked what, what ACL tags were applied, how many results)

In Phase 0, these tables were empty. Phase 5 enforced tier-0-only filtering for unauthenticated callers. **Phase 7 adds authenticated, squad-scoped ACLs:**

- **Keycloak** (`docker compose` service, `localhost:8081`, realm `teamrag`) is the identity provider. Dev users `alice` (member of `squad-payments`) and `bob` (no groups) ship in the seeded realm for local testing.
- **`POST /query`, `POST /document`, and `/v1/chat/completions`** accept an optional `Authorization: Bearer <JWT>` header. `teamrag.auth` validates the token against Keycloak's JWKS (issuer/audience checked via `OIDC_ISSUER`/`OIDC_AUDIENCE`); a malformed/expired/wrong-signature token returns **401**. No token (or `OIDC_ISSUER` unset) falls back to unauthenticated tier-0-only visibility — Phase 5 behavior is preserved.
- **`teamrag.acl.AclFilterMode`** has two modes: `UNAUTHENTICATED_TIER_0` and `AUTHENTICATED_GROUPS`. `resolve_acl_context(identity)` maps a validated `UserIdentity` (with `groups: tuple[str, ...]` from the JWT `groups` claim) to `AUTHENTICATED_GROUPS`, and `qdrant_filter_for_mode(mode, user_groups)` widens the Qdrant filter to `tier-0 ∪ user_groups` — so a query time check is effectively `user's groups ∩ chunk's acl_tags ≠ ∅` (tier-0 always included).
- **`resource_acl_mappings`** table (`source_type`, `resource_key`, `acl_tags`) maps an external resource (e.g., a GitHub repo, Confluence space, or Jira project) to the ACL tags its chunks should carry. Ingestion connectors look up this table at chunk-write time to tag chunks beyond the tier-0 default.
- **`python -m teamrag.sync`** refreshes `resource_acl_mappings` — either by fetching Keycloak group membership/attributes (`fetch_keycloak_groups` + `mappings_from_groups`, which reads the multi-valued group attributes `repos` / `spaces` / `channels` / `rooms` / `projects` → `github` / `confluence` / `teams` / `webex` / `jira`), or by seeding a mapping directly via `--seed source_type:resource_key:tag1,tag2` (repeatable flag). See README "Phase 7" section for usage.
- **`audit_log`** rows record `caller_id` (JWT subject, or `"anonymous"`) and `acl_tags_applied` (the resolved filter tags) per query — see `tests/integration/test_phase7_squad_acls.py::test_audit_log_records_squad_query`.
- **MCP server** forwards an optional bearer token (`TEAMRAG_BEARER_TOKEN` env var) to the gateway on every tool call, so IDE assistants inherit the caller's ACL scope.

### Ingestion Connectors (`src/teamrag/ingest/`)

One module per source, sharing the chunk → embed → upsert pipeline in `pipeline.py`:

```
src/teamrag/ingest/
├── __main__.py                     # CLI: python -m teamrag.ingest <confluence|github|jira|teams|webex|chat> [--poll]
├── pipeline.py                     # embed_chunks, upsert_to_qdrant, write_to_postgres / write_chat_thread_to_postgres
├── acl_mapping.py                  # resource_acl_mappings lookup applied to chunks before upsert (Phase 7)
├── confluence.py                   # Confluence pages → Markdown chunks (Phase 1)
├── github.py                       # GitHub PRs → chunks (Phase 2)
├── teams.py / webex.py             # chat threads → one chunk per thread (Phase 6)
├── chat_thread.py / chat_signal.py # shared chat assembly/signal helpers (Phase 6)
└── jira.py                         # Jira Cloud tickets → one chunk per ticket (Phase 8)
```

**Jira** (`jira.py`, Phase 8) — `JiraClient` (async httpx, email + API token) runs a JQL search over `JIRA_PROJECT_KEYS` (`project in (...) ORDER BY updated DESC`, capped by `JIRA_MAX_ISSUES`) and fetches comments per issue. `adf_to_text` recursively flattens Jira Cloud's ADF (Atlassian Document Format) JSON bodies to plain text. `assemble_issue_document` builds one markdown document per ticket (title, description, comments, resolution line); `chunk_issue_document` turns it into a single chunk with citation `source_url = {JIRA_URL}/browse/{issue_key}`, stable id `sha256("jira:{issue_key}:0")`, and metadata `issue_key`, `project_key`, `status`, `assignee`, `reporter`, `labels`, `epic`, `resolution`, `linked_prs` (GitHub PR URLs regex-extracted from the document, the Phase 2 cross-link). `python -m teamrag.ingest jira [--poll]` reuses the generic single-chunk Postgres writer (`write_chat_thread_to_postgres`, `source_type="jira"`); polling is the re-index-on-status-change mechanism — a status change bumps `updated`, so the next poll re-fetches and idempotently overwrites the same stable chunk id. ACL: resource key = `project_key`, gated via the Keycloak group attribute `projects` (see "ACL Model" above).

### Health Checks

All Docker services have health checks:
- **Postgres:** `pg_isready` (5s timeout, 10s interval)
- **Qdrant:** HTTP GET `/healthz` (5s timeout, 10s interval)
- **TEI:** HTTP GET `/health` (10s timeout, 30s interval) — takes ~2 min on first run to load BGE-M3

Always wait for `docker compose ps` to show all services as `healthy` before running tests. The integration tests gracefully skip if services are unreachable, but it's better to verify upfront.

### Postgres Credentials

Local development defaults (in `.env.example`):
- User: `teamrag`
- Password: `teamrag`
- Database: `teamrag`
- URL: `postgresql+asyncpg://teamrag:teamrag@localhost:5432/teamrag`

For production, override in `.env` (never commit real credentials).

---

## Debugging Tips

### "Connection refused" on Postgres or Qdrant

Ensure Docker services are running and healthy:
```bash
docker compose up -d
sleep 5  # wait for healthchecks to pass
docker compose ps
```

If a service is stuck in `starting`, check logs:
```bash
docker compose logs postgres
docker compose logs qdrant
docker compose logs tei
```

### Test Failures

Always run with Docker up:
```bash
docker compose up -d
uv run pytest tests/ -v
```

Tests can gracefully skip if Qdrant is unavailable, but Postgres queries will fail. Ensure Postgres is healthy before running tests.

### FastAPI won't start

Check:
1. Config is valid: `uv run python -c "from teamrag.config import settings; print(settings)"`
2. DATABASE_URL is set: `echo $DATABASE_URL`
3. Port 8000 is free: `lsof -i :8000`
4. Dependencies installed: `uv sync`

### Alembic "Can't locate revision identified by"

This means the migration history is corrupt or a file was deleted. Recover with:
```bash
alembic stamp head  # forcibly set current revision
alembic upgrade head  # re-apply everything (careful with production!)
```

---

## Testing Strategy

### Integration Tests Only (for Now)

Phase 0 has only integration tests (`tests/integration/test_phase0.py`). They require live Docker services and test the full stack:

1. **API tests** — use ASGI transport (no live server needed):
   ```python
   async with AsyncClient(transport=ASGITransport(app=app), ...) as client:
       response = await client.get("/health")
   ```

2. **Qdrant tests** — hit live Qdrant API; skip gracefully if unreachable

3. **Postgres tests** — use live Postgres via SQLAlchemy

Later phases may add unit tests and fixtures, but start integration-first.

### conftest.py Setup

`conftest.py` sets `DATABASE_URL` as an environment variable before any imports. This ensures the app picks it up. Add shared fixtures here (e.g., test database session, Qdrant client, etc.).

---

## When Adding New Code

1. **New API endpoint?** Create a router in `api/`, add tests in `tests/integration/`.
2. **New database feature?** Update `db/models.py`, run `uv run alembic revision --autogenerate`, test migrations.
3. **New environment variable?** Add to `config.py` and `.env.example`.
4. **New dependency?** Add to `pyproject.toml` dependencies, then run `uv sync` to update `.venv` and `uv.lock`.
5. **Query logic later?** Keep it out of routers; put it in a `services/` module or similar.
6. **Qdrant integration?** Use `AsyncQdrantClient`; follow the pattern in lifespan handler.

Always:
- Keep endpoints async
- Use Pydantic models for requests/responses
- Test with Docker services running via `uv run pytest`
- Commit `.env.example` changes alongside code changes
- Run `uv sync` after any `pyproject.toml` changes

---

## Specs & Roadmap

- **`specs/mission.md`** — why TeamRag exists, what success looks like
- **`specs/tech-stack.md`** — component choices and rationale
- **`specs/roadmap.md`** — all 13 phases with acceptance criteria

These are living documents; changes should be rare but discussed with the team. Phase 0 requirements are in `specs/2026-05-08-phase-0-core-infrastructure/`.

---

## Further Reading

- README.md — quick start, high-level architecture
- src/teamrag/main.py — app entry point and lifespan logic
- src/teamrag/config.py — all configuration via env vars
- alembic/versions/ — schema evolution history
- tests/integration/test_phase0.py — what the system must do
