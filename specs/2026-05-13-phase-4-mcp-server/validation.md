# Phase 4 — MCP server: validation (merge readiness)

## Principles

Validation proves the MCP server is a faithful, reliable adapter over the FastAPI retrieval layer (`specs/mission.md`, `specs/tech-stack.md`) and meets Phase 4 in `specs/roadmap.md` without regressing existing behavior.

## Automated (pytest)

- **Unit / integration tests** (as appropriate for the codebase):
  - Tool handlers: `search_knowledge` maps gateway responses to MCP chunk payloads; invalid inputs produce clear errors.
  - `get_document`: URL parsing, empty result, and happy path against test doubles or live gateway in integration suite—follow existing project patterns (`tests/integration`, Docker stack when required).
- **Transport smoke** where automatable:
  - stdio: subprocess or SDK test harness that starts the server and performs a handshake / list-tools (no full IDE required).
  - HTTP SSE: async client test that completes at least one tool round-trip against a test server instance (or documented skip if CI cannot bind ports—prefer running in CI when feasible).
- **Regression:** Full `uv run pytest` passes on the branch with services up per `CLAUDE.md` / project README.

## Manual (IDE / operator)

- **Cursor or Claude Code:** Load example `mcp.json` / `claude_desktop_config.json` (adjusted for local paths).
- **stdio path:** Confirm both tools appear and run without stack traces; `search_knowledge` returns a valid payload against a running gateway.
- **SSE path (if deployed locally):** Confirm same tools work through HTTP SSE configuration documented in this feature’s README/spec pointer.

## Merge bar (all required)

1. Automated suite green in CI (or documented equivalent if CI lacks Docker—then reviewers run pytest locally and note it in the PR).
2. Manual checklist above executed once per release candidate and noted in the PR description (who ran it, which client).
3. Example config files in repo match actual flags, module path, and env var names shipped in the PR.
4. Roadmap **Done when** satisfied in a realistic dev setup: an IDE agent can call `search_knowledge` and obtain chunks suitable for a grounded answer once indexed content exists (empty results acceptable when the gateway returns none).
