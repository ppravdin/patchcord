# Architecture

Canonical guide: <https://patchcord.dev/docs/architecture>

Source of truth for the published page:

- `patchcord-site repo, src/app/docs/architecture/page.mdx`

Use this repo note only for implementation pointers:

- MCP tool handlers: `patchcord/server/tools.py`
- Presence, storage, cleanup, and Supabase helpers: `patchcord/server/helpers.py`
- Server configuration: `patchcord/server/config.py`
- HTTP routes and middleware: `patchcord/server/app.py`
- Legacy direct mode: `patchcord/direct/server.py`
- Database schema: `migrations/`

Key model reminders:

- Data tables include `agent_messages`, `agent_registry`, `rate_limit_bans`, `bearer_tokens`, and the OAuth tables.
- The published architecture doc also covers the URL relay flow, inbox gate behavior, IDE bearer-only clients, and direct mode.

When the system model changes, update the website doc first. Keep this file as an index, not a second architecture write-up.
