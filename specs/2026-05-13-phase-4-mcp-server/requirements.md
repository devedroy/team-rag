# Phase 4 — MCP server: requirements

## Context

Phase 4 exposes TeamRag to AI coding assistants (Claude Code, Cursor, Continue.dev) as an MCP server over the same retrieval backend described in `specs/mission.md` and `specs/tech-stack.md`. Humans and agents remain two first-class consumers; the MCP layer wraps the existing FastAPI retrieval contract rather than duplicating retrieval logic.

## Scope (in)

Aligned with **Phase 4 — MCP server** in `specs/roadmap.md`:

- **MCP Python SDK** server that wraps the retrieval API (calls into the same behavior as `POST /query` and any supporting endpoints needed for document fetch).
- **Tools:**
  - `search_knowledge(query: str) → chunks[]` — semantic search over all indexed content.
  - `get_document(source_url: str) → chunk[]` — all chunks for a specific source URL.
- **Transports:**
  - **stdio** for local IDE use (Claude Code, Cursor, Continue.dev).
  - **HTTP SSE** for team-wide / remote deployment.
- **Example configuration** in-repo: `mcp.json` and `claude_desktop_config.json` patterns documented so operators can wire the server without guesswork.

## Out of scope (this slice)

- Tiered ACL enforcement (Phase 5+); assume current gateway behavior unless already present.
- New ingestion connectors or changes to chunk schema beyond what existing APIs return.
- Chat UI changes (Phase 3 territory).
- Production hardening beyond what the roadmap implies for this phase (e.g. full SSO for MCP HTTP is not required here unless already standard for the gateway).

## Decisions

| Topic | Decision |
| --- | --- |
| Retrieval source of truth | FastAPI gateway remains canonical; MCP tools are thin adapters (no second retrieval implementation). |
| Stack | MCP Python SDK; transports stdio + HTTP SSE per `specs/tech-stack.md`. |
| Auth | No new auth model specified for Phase 4 in the roadmap; document how MCP HTTP deployment inherits or assumes existing gateway access patterns (env URLs, network placement). Revisit when Phase 5+ adds query-time ACLs. |
| Naming | Tool names and shapes match roadmap (`search_knowledge`, `get_document`) unless implementation constraints force minor adjustments—if so, document in plan and keep examples in sync. |

## References

- `specs/mission.md` — shared knowledge layer; agents retrieve team context via MCP.
- `specs/tech-stack.md` — FastAPI gateway, MCP Python SDK, stdio + SSE/HTTP transports.
- `specs/roadmap.md` — Phase 4 acceptance: Claude Code can call `search_knowledge` and get a grounded answer from team docs.
