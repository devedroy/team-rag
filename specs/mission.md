# TeamRag Mission

TeamRag is the shared knowledge layer for the engineering team — queryable by engineers through a chat UI and by AI coding assistants (Claude, Cursor, and any MCP-compatible tool) through a standard MCP server.

---

## What it does

TeamRag ingests the team's real knowledge: Slack threads, architecture docs, pull requests, tickets, meeting notes, and decisions. It indexes that knowledge so any question — human or machine — gets an answer grounded in what the team actually said, decided, and built.

---

## Why it exists

Knowledge in software teams is ephemeral. It lives in a Slack thread from 2022, in a PR description nobody reads, in a retro note nobody filed. When the person who made the call leaves, the reasoning leaves with them.

TeamRag makes that knowledge permanent, searchable, and citable.

---

## Two first-class consumers

**Humans** — Engineers, PMs, and new hires ask questions in a chat interface and get answers with citations linking back to the original source (Slack thread, Confluence page, PR, Jira ticket).

**AI agents** — Coding assistants (Claude Code, Cursor, Continue.dev) call TeamRag as an MCP tool to ground their suggestions in team context: architecture decisions, past experiments, runbooks, ownership maps.

Both consumers query the same retrieval backend. The MCP server and the chat UI are two interfaces over one knowledge base.

---

## What it is not

- Not a documentation tool. TeamRag consumes docs but does not author them.
- Not a code completion engine. It retrieves context; the IDE's LLM synthesizes.
- Not a replacement for Confluence or Notion. It indexes them.

---

## Success criteria

- A new engineer can answer "why did we choose X?" without pinging a senior.
- An AI agent working in the codebase can retrieve the relevant design doc or post-mortem without being explicitly pointed to it.
- The team stops repeating the same questions in Slack.
