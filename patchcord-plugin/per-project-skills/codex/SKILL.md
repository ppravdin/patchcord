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

- `inbox(all_agents?)` - read pending messages, current identity, and recently active agents. `all_agents=true` includes inactive agents. Returns a `groups` list (messages grouped by thread) alongside the legacy `pending` flat list. Presence tells you whether to wait for a reply after sending, not whether to send.
- `send_message(to_agent, content, thread?)` - send a message. Comma-separated for multiple: `send_message("backend, frontend", "hello")`. Use `@username` for cross-user Gate messaging. `thread` is an optional slug to start or join a named thread: `send_message("backend", "...", thread="auth-migration")`. Messages support up to 50,000 characters - send full content, specs, and code as-is. Never summarize or truncate.
- `reply(message_id, content?, defer?, resolve?)` - reply to a received message. Auto-inherits the thread of the original. `defer=true` keeps the original visible in inbox for later (survives context compaction). `resolve=true` closes the thread — stamps `thread_resolved_at` and notifies sender. Content is optional: use `reply(message_id, resolve=true)` to silently close without sending.
- `wait_for_message(timeout_seconds?)` - block until incoming message arrives. Default 5 minutes. Known to error intermittently - if it fails, poll inbox() every 10-15 seconds as fallback.
- `attachment(...)` - upload, download, or relay files between agents (see File sharing below)
- `recall(limit?, from_agent?, thread_id?)` - view recent message history including already-read messages. `from_agent` filters by sender. `thread_id` filters to a specific thread. For debugging only, not routine use.
- `unsend(message_id)` - take back a message before the recipient reads it

## Do the work, never just acknowledge

When you receive a message from another agent:

1. Do the task described in the message first. Update the file. Write the code. Fix the bug. Whatever it asks - do it.
2. Then reply with what you did. File paths, line numbers, concrete changes.
3. Never reply with only an acknowledgment. "Got it", "Will do", "Ready" are not acceptable as standalone replies.

The user can undo any change in seconds. A wrong action costs nothing. A useless ack wastes everyone's time.

If you genuinely cannot act (missing file access, need credentials, ambiguous target): say specifically what's blocking you.

If you can't do it right now: use `reply(message_id, "reason", defer=true)` to keep the message visible for later. Never silently skip a message.

## Startup

Call `inbox()` once at session start.

If there are pending actionable messages:

1. Do the work described in each message
2. Reply with what you did
3. Tell the user what came in and what you did about it

Do not ask the user for permission to reply unless the requested action is destructive or requires secrets you do not have.

## Threads

Named threads group related messages between a pair of agents. Use them for multi-turn tasks that need their own context.

- **Start**: `send_message("backend", "track this here", thread="deploy-review")`
- **Reply stays in thread automatically** — `reply()` inherits `thread_id` from the message you're replying to.
- **Close**: `reply(message_id, "done", resolve=true)` — closes the thread and notifies sender.
- **Filter history**: `recall(thread_id="<uuid>")` — only messages in that thread.

`inbox()` `groups` field clusters pending messages by thread. Each group: `{ thread_id, thread_title, messages }`. `thread_id: null` = pair-level.

## Sending workflow

1. `inbox()` - clear pending messages that block outbound sends. Note who's online (determines whether to wait after sending).
2. `send_message("agent", "specific question with paths and context")` - or `"agent1, agent2"` for multiple, or `"@username"` for cross-user Gate messaging. Add `thread="slug"` to group messages in a named thread.
3. If recipient is online: `wait_for_message()` - stay responsive for the response. If offline: skip the wait, tell the human the message is queued.

Always send regardless of online/offline status. Messages are stored and delivered when the recipient checks inbox. Never refuse to send because an agent appears offline.

After sending to an offline agent, tell the human: "Message sent. [agent] is not currently active - ask them to check their inbox."

If send_message fails with a send gate error: call inbox(), reply to or resolve all pending messages, then retry the send.

## Receiving workflow

1. Read the message from `inbox()` or `wait_for_message()`. Check `message.thread` / `message.thread_id` if present.
2. Do the work - use real code, real files, real results from your project
3. Reply with the right flag:
   - `reply(message_id, "done: [details]")` — work done, sender might follow up. Thread auto-inherited.
   - `reply(message_id, "done: [details]", resolve=true)` — work done, thread closed.
   - `reply(message_id, resolve=true)` — silently close without sending anything.
   - `reply(message_id, "ack, prioritizing [other task] first", defer=true)` — acknowledged but work not done yet. Message stays in your inbox as a reminder.
4. If sender is online: `wait_for_message()` for follow-ups

When you have multiple pending messages, prioritize by urgency. Use `defer=true` for tasks you'll do later — if you reply without doing the work and don't defer, the message vanishes from your inbox and you will never remember to do it.

## Cross-user messaging (Gate)

To message a user outside your namespace, use `@username` as the to_agent. Example: `send_message("@maria", "hello")`. The message goes through their Gate - connection approval and guardrails apply. If the connection isn't approved yet, your message is held pending their approval (cap 5, 7-day TTL).

## File sharing

Three modes:

**Relay from URL (preferred for public files):**
```
attachment(relay=true, path_or_url="https://example.com/file.md", filename="file.md")
```
Server fetches the URL and stores it. ~50 tokens instead of thousands for the file content.

**Presigned upload (preferred for local files):**
```
attachment(upload=true, filename="report.md") -> returns {url, path}
curl -X PUT -H "Content-Type: text/markdown" --data-binary @/path/to/report.md "<url>"
```
Then send the `path` to the other agent. No base64, no token waste.

**Inline base64 (last resort — small generated content only):**
```
attachment(upload=true, filename="notes.txt", file_data="<base64>")
```
Base64 adds ~33% overhead and wastes context tokens. Never use this for files on disk — use presigned upload above instead.

**Downloading:**
```
attachment(path_or_url="namespace/agent/timestamp_file.md")
```

Send the returned `path` to the other agent in your message so they can download it.

## Rules

- Do the work first, reply second. Never reply before completing the task.
- Do not send ack-only replies to "ok", "noted", "seen", "thanks", or conversation-ending signals. Just read them and move on.
- Do not show raw JSON to the user unless they explicitly ask for it.
- Use `agent@namespace` when the online list shows multiple namespaces for the same agent name.
- Keep Patchcord config project-local. Do not rely on global shell exports.
- If Patchcord tools are missing in Codex, diagnose MCP config rather than pretending a plugin should provide them.
- MCP tools are cached at session start. New tools deployed after your session began are invisible until you start a new session.
- Agent names change frequently. Do not memorize or hardcode them. Check inbox() for recent activity. When unsure which agent to message, ask the human.
