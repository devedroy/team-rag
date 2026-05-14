# Microsoft Teams and Webex Messaging ingestion (Phase 6)

This document describes how to register bots, grant API access, and run the chat connectors shipped in Phase 6. Ingestion is **backfill + periodic polling** only (no Microsoft Graph change notifications and no Webex webhooks in this phase).

## Security and ACL deferral (read before enabling)

Until **Phase 7 (squad-level ACLs)** ships, every ingested chat thread is tagged **`tier-0`** at ingest time (same default as Confluence and GitHub). There is **no** per-channel or per-space privacy mapping in Phase 6.

**Do not add the bot to Teams channels or Webex spaces whose contents must not be visible to unauthenticated `POST /query` / MCP callers under the current tier-0 policy.** Restrict access by only installing the bot where you accept tier-0 visibility.

## Environment variables

See root `.env.example` for variable names. Required for Teams:

- `TEAMS_TENANT_ID` — Entra ID (Azure AD) tenant GUID.
- `TEAMS_CLIENT_ID` — Application (client) ID of the registered app.
- `TEAMS_CLIENT_SECRET` — Client secret for client-credentials flow.
- `TEAMS_BOT_USER_ID` — Azure AD **object ID** of the bot’s user identity (the user the app acts as when reading `joinedTeams`).

Required for Webex:

- `WEBEX_BOT_TOKEN` — Bot access token from a Webex integration.
- `WEBEX_ORG_ID` — Organization identifier used in chunk metadata (from Webex admin or API).

Optional tuning (shared):

- `CHAT_INGEST_POLL_INTERVAL_SECONDS` (default `300`) — delay between CLI `--poll` cycles.
- `CHAT_INGEST_MIN_PARTICIPANTS` (default `5`) — minimum distinct **human** authors in a Teams channel history sample, or human memberships in a Webex room, before any threads from that channel/room are indexed.
- `CHAT_INGEST_SKIP_NO_REPLIES` (default `true`) — skip single-message threads with no replies.
- `CHAT_INGEST_MAX_CHANNEL_ROOTS` — cap of root messages scanned per Teams channel per run (default `200`).
- `CHAT_INGEST_WEBEX_MAX_MESSAGES` — cap of messages loaded per Webex room per run (default `500`).
- `WEBEX_WEB_CLIENT_BASE` (default `https://app.webex.com`) — base URL used to build `source_url` deep links for Webex threads.

## Microsoft Teams (Microsoft Graph)

1. **Register an application** in the Microsoft Entra admin center for your tenant.
2. **Application permissions** (admin consent required), typical set for channel read:
   - `ChannelMessage.Read.All`
   - `Team.ReadBasic.All` or `Group.Read.All` as needed for team enumeration
   - `User.Read.All` (or equivalent) if required for `users/{id}/joinedTeams` in your tenant policy
3. **Create a client secret** and place `TEAMS_CLIENT_ID`, `TEAMS_CLIENT_SECRET`, and `TEAMS_TENANT_ID` in `.env`.
4. **Install the bot** (Teams app) into each team/channel you want indexed. Only resources the bot can access are returned by Graph.
5. Set `TEAMS_BOT_USER_ID` to the bot user’s Entra object ID (the identity that appears in `joinedTeams`).

### Run ingest

```bash
uv run python -m teamrag.ingest teams
```

Continuous polling:

```bash
uv run python -m teamrag.ingest teams --poll
```

## Webex Messaging

1. Create a **Webex bot** (or integration) at [Webex for Developers](https://developer.webex.com/) and copy the bot access token into `WEBEX_BOT_TOKEN`.
2. Set `WEBEX_ORG_ID` to your organization id (metadata only; required by this deployment’s config gate).
3. Add the bot to **group spaces** you want indexed (DMs are out of scope for Phase 6).

### Run ingest

```bash
uv run python -m teamrag.ingest webex
```

Polling:

```bash
uv run python -m teamrag.ingest webex --poll
```

## Combined run (Teams then Webex)

```bash
uv run python -m teamrag.ingest chat
```

With polling:

```bash
uv run python -m teamrag.ingest chat --poll
```

## Retrieval and citations

- `POST /query` and MCP tools return **`ChunkResult`** fields only: `content`, `source_url`, `page_title`, `score`.
- For chat chunks, **`page_title`** is the Teams **channel display name** or the Webex **space title** (same pattern as GitHub’s `pr_title` → `page_title`).
- Full platform metadata (`source`, `thread_id`, tenant/org ids, participants, etc.) is stored in **Postgres** `chunks.metadata` and in the **Qdrant payload** for operators and tests; it is not exposed in the public JSON response for this phase.

## Scopes and support

Exact Graph permission names can vary by tenant policy; adjust the Entra app registration to match the errors returned by Graph (403/401). Webex rate limits may require increasing `CHAT_INGEST_POLL_INTERVAL_SECONDS` or narrowing rooms via bot membership (Phase 6 does not implement allowlists—membership is controlled in Teams/Webex admin UIs).
