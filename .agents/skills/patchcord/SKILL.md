---
name: patchcord
description: >
  Cross-agent messaging for Codex via the Patchcord MCP server. Use when the
  user mentions other agents, inbox state, sending messages, who's online, or
  cross-machine coordination.
---

# Patchcord for Codex

You are connected to Patchcord through a normal MCP HTTP server entry in Codex.

There is no Codex plugin. Patchcord behavior comes from this skill plus the
project's MCP config.

## Tools available

- `inbox()` — read pending messages and see who is online
- `send_message(to_agent, content)` — send a message
- `reply(message_id, content)` — reply to a received message
- `reply(message_id, content, defer=true)` — reply but keep the original message visible as "deferred" in the inbox (use when the message needs later attention or another agent should handle it)
- `wait_for_message()` — block until any incoming message arrives
- `upload_attachment(filename, mime_type)` / `get_attachment(path_or_url)` — share files
- `relay_url(url, filename, to_agent)` — fetch a URL and send it as an attachment
- `recall_message(message_id)` — unsend if unread

## Startup rule

Call `inbox()` once at session start to orient.

If there are pending actionable messages:

1. Read them
2. Reply immediately
3. Tell the user what came in and what you answered

Do not ask the user for permission to reply unless the requested action is destructive or requires secrets you do not have.

## Sending workflow

1. `inbox()` — check who is online
2. `send_message("agent", "specific question with paths and context")`
3. `wait_for_message()` — stay responsive for the response

## Receiving workflow

1. Read the message from `inbox()` or `wait_for_message()`
2. Use the real code / files / results from your project
3. `reply(message_id, "concrete answer")`
4. `wait_for_message()` again when follow-up is expected

## Rules

- Reply immediately to actionable incoming messages.
- Do not send ack-only replies to `ok`, `noted`, `seen`, `thanks`, or other conversation-ending signals.
- Do not show raw JSON to the user unless they explicitly ask for it.
- Use `agent@namespace` when the online list shows multiple namespaces for the same agent name.
- Keep Patchcord config project-local. Do not rely on global shell exports.
- If Patchcord tools are missing in Codex, diagnose MCP config rather than pretending a plugin should provide them.
