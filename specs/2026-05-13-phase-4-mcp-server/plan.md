# Phase 4 — MCP server: implementation plan

Numbered groups are execution order; complete lower groups only when upper groups’ exit criteria are met.

## 1. Dependencies and MCP skeleton

- Add MCP Python SDK (and any minimal HTTP/SSE helpers) per `pyproject.toml` / `uv` workflow.
- New package or module layout for the MCP server (separate entrypoint from FastAPI app is fine; shared client code for calling the gateway is encouraged).
- **Exit:** Importable MCP server module; no tools wired yet; `uv sync` and existing tests still green.

## 2. `search_knowledge` wired to retrieval

- Implement tool handler: accept `query: str`, call gateway `POST /query` (or shared internal client) with appropriate `top_k` / defaults from config.
- Map response to MCP-friendly chunk list (fields consistent with API models).
- **Exit:** With stack running, tool returns structured chunks (may be empty in Phase 0–early phases) without errors.

## 3. `get_document` by source URL

- Implement tool handler: resolve `source_url` to chunks (gateway endpoint or DB/Qdrant path as chosen—prefer reusing FastAPI if an endpoint exists or add a narrow internal route if required).
- **Exit:** Given a known source URL from indexed content (post–ingestion phases), tool returns all chunks for that source; errors are explicit and logged.

## 4. stdio transport entrypoint

- Provide a `python -m …` or script entrypoint that runs the MCP server over stdio for local IDE configuration.
- **Exit:** Documented command matches example `mcp.json` / Cursor config; local smoke: server starts and registers tools.

## 5. HTTP SSE transport (team-wide)

- Expose MCP over HTTP SSE as required by the SDK / deployment pattern chosen.
- Configuration via env (host, port, gateway base URL) consistent with `specs/tech-stack.md`.
- **Exit:** Remote client (or curl-level smoke) can complete a tool call session over SSE.

## 6. Documentation, example configs, and smoke path

- Add `mcp.json` and `claude_desktop_config.json` examples to the repo (placeholders for paths/URLs).
- README or short doc section: how to run stdio vs SSE, required env, how to point at local vs deployed gateway.
- **Exit:** A new teammate can follow docs to attach Claude Code/Cursor and invoke both tools; pointers to `validation.md` for merge checks.
