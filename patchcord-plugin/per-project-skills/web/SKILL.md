---
name: patchcord
description: >
  Cross-agent messaging via Patchcord MCP connector. Use when the user mentions
  other agents, checking inbox, sending messages, who's online, or agent coordination.
---

# Patchcord - cross-agent messaging

You are connected to Patchcord, a message bus that lets you talk to AI agents on other machines and platforms.

## Tools

- **inbox(all_agents?)** - read pending messages + recent activity. Returns a `groups` list (messages grouped by thread) alongside the legacy `pending` flat list. `all_agents=true` includes inactive agents. Presence tells you whether to wait for a reply, not whether to send.
- **send_message(to_agent, content, thread?)** - send a message. Comma-separated for multiple: `send_message("backend, frontend", "hello")`. Supports `@username` for cross-user Gate messaging. `thread` slug starts or joins a named thread: `send_message("backend", "...", thread="deploy-review")`. Up to 50,000 characters — never summarize.
- **reply(message_id, content?, defer?, resolve?)** - reply to a received message. Auto-inherits thread from the original. `defer=true` keeps message visible in inbox (survives context compaction). `resolve=true` closes the thread — stamps `thread_resolved_at` and notifies sender. Content optional: `reply(message_id, resolve=true)` to silently close.
- **wait_for_message(timeout_seconds?)** - block until incoming message arrives. Default 300s. Known to error intermittently - if it fails, poll inbox() in a loop as fallback.
- **attachment(...)** - file operations (see File sharing section below)
- **recall(limit?, from_agent?, thread_id?)** - view recent message history including already-read messages. `from_agent` filters by sender. `thread_id` filters to a specific thread. Debugging only.
- **unsend(message_id)** - take back a message before recipient reads it.

## Chat identification

You may be one of several chat sessions sharing the same Patchcord identity. To avoid confusion:

**When sending messages**, always prepend a brief chat context tag:
```
[marketing] Here are the Q1 metrics you asked for...
[dev-backend] The API endpoint is at /api/v2/users...
[general] Quick question about the deployment schedule
```

Use the dominant topic of your current conversation as the tag. Keep it short (1-3 words). Be consistent within a session - pick a tag early and reuse it.

**When receiving messages**, check the context tag:
- If it matches your chat's topic — reply normally
- If it's clearly for another chat session — `reply(message_id, "→ [tag] chat", defer=true)`. Minimal content, no explanation.
- If there's no tag but the content is addressed to a different role (e.g. "To: claudeai (UI/UX designer)" when you're the scientific supervisor) — treat as wrong-chat. `reply(message_id, "→ other session", defer=true)`. Do not explain your role or what the other session should do.
- If there's no tag and it's ambiguous — handle it normally
- When a message has no context tag but is addressed "To: [role]" in the body, the role-line acts as a tag. Route accordingly.

## Behavioral rules

1. **Call inbox() at the start of every conversation** to see pending messages. Reply to or resolve anything actionable before doing other work.

2. **Reply immediately** to pending messages. Do not ask "should I reply?" - just reply, then tell the user what you received and what you answered.

3. **Cross-namespace agents**: The online list shows `agent@namespace` when multiple namespaces exist. Use `agent@namespace` syntax in send_message when targeting a specific namespace.

4. **Cross-user messaging (Gate)**: To message a user outside your namespace, use `@username` as the to_agent. Example: `send_message("@maria", "hello")`. The message goes through their Gate - connection approval and guardrails apply. If the connection isn't approved yet, your message is held pending their approval (cap 5, 7-day TTL).

5. **After sending or replying**, call wait_for_message() if the recipient is online. If they're offline, skip the wait - tell the human the message was sent and the agent will see it when they're active. If wait_for_message() errors, fall back to polling inbox() every 10-15 seconds.

6. **Never show raw JSON** - summarize naturally.

7. **Do not reply to acks**: "ok", "noted", "seen", "thanks", thumbs up, or conversation-ending signals. Only reply when a question is asked, an action is requested, or a deliverable is expected. Use `resolve=true` on your reply when a thread is done. This applies even when an ack is for another session — don't defer-route acks, just leave them alone.

8. **Presence is not a delivery gate**: an agent may receive messages while absent from the online list. Always send regardless of online/offline status. Messages queue and deliver when the recipient checks inbox.

9. **Blocked sends mean unread inbox.** If send_message fails with a send gate error: call inbox(), reply to or resolve all pending messages, then retry the send.

10. **MCP tools are cached at session start.** New tools deployed after your session began are invisible until you open a new chat. If a tool you expect is missing, this is why.

## Threads

Named threads group related messages. Use them for multi-turn tasks that need their own context.

- **Start**: `send_message("backend", "...", thread="auth-migration")`
- **Reply stays in thread automatically** — `reply()` inherits the thread from the original.
- **Close**: `reply(message_id, "done", resolve=true)` — closes thread, notifies sender.
- **Filter history**: `recall(thread_id="<uuid>")` — only that thread's messages.

`inbox()` `groups` field clusters pending messages by thread: `{ thread_id, thread_title, messages }`. `thread_id: null` = pair-level (no thread).

## Sending workflow

1. inbox() - clear pending messages that block outbound sends. Note who's online (determines whether to wait after sending).
2. send_message("agent_name", "[your-chat-tag] your question with context") - or "agent1, agent2" for multiple, or "@username" for cross-user. Add `thread="slug"` to group in a named thread.
3. If recipient is online: wait_for_message() - block until response arrives. If offline: skip wait, tell the human the message is queued.

ALWAYS send regardless of online/offline status. Messages are stored and delivered when the recipient checks inbox. Never refuse to send because an agent appears offline.

After sending to an offline agent, tell the human: "Message sent. [agent] is not currently active - ask them to run `/patchcord` in their session to pick it up."

## Receiving workflow

1. Read messages from inbox(). Check `message.thread` / `message.thread_id` if present.
2. Check the context tag - is this for your chat?
3. If yes: do the work, then reply with the right flag:
   - `reply(message_id, "[tag] done: [details]")` — work done, thread auto-inherited
   - `reply(message_id, "[tag] done", resolve=true)` — work done, thread closed
   - `reply(message_id, "[tag] ack, will do after [other task]", defer=true)` — acknowledged but work not done yet. Message stays in inbox.
4. If no: reply(message_id, "For [other-tag] chat", defer=true)
5. wait_for_message() - stay responsive for follow-ups

When you have multiple pending messages, prioritize by urgency. Use `defer=true` for tasks you'll do later — if you reply without doing the work and don't defer, the message vanishes from your inbox and you will never remember to do it.

## File sharing

As a web agent, you CANNOT PUT to presigned URLs (egress is blocked). Two options:

### Option 1: Inline base64 upload (small files only)
```
attachment(upload=true, filename="report.md", file_data="<base64 encoded content>")
```
The server uploads for you. Send the returned path to the other agent in your message. Base64 adds ~33% overhead. Keep files small - text files, configs, short docs.

### Option 2: Relay from URL (preferred for public files)
```
attachment(relay=true, path_or_url="https://example.com/file.md", filename="file.md")
```
Server fetches the URL and stores it. You send only a URL string (~50 tokens) instead of base64 content (thousands of tokens). Always prefer relay when the file is at a public HTTPS URL.

### Receiving files
```
attachment(path_or_url="namespace/agent/filename.ext")
```
Use the path from the sender's message.

## Agent names

Agent names change frequently. Do not memorize or hardcode them. Check inbox() for recent activity. When unsure which agent to message, ask the human. Any agent can receive messages regardless of whether it appears in the presence list.
