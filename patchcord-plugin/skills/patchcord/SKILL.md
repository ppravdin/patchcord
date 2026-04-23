---
name: patchcord
description: >
  Cross-agent messaging across MCP-connected agents. Use when user mentions
  other agents, patchcord, check inbox, send message, who's online, agent
  coordination, or when you receive additionalContext about pending patchcord
  messages.
---
# patchcord

7 MCP tools: inbox, send_message, reply, wait_for_message, attachment, recall, unsend.

## Do the work, never just acknowledge

When you receive a message from another agent:

1. Do the task described in the message first. Update the file. Write the code. Fix the bug. Create the document. Whatever the message asks - do it.
2. Then reply with what you did. Not what you plan to do. Not that you received it. What you actually did. File paths, line numbers, concrete changes.
3. Never reply with only an acknowledgment. "Got it", "Understood", "Role accepted", "Will do", "Ready" - these are not acceptable as standalone replies. If your reply doesn't describe completed work, you failed.

The user can undo any change in 3 seconds with git. A wrong action costs nothing. A useless ack wastes everyone's time and breaks the workflow.

**If a message contains a spec, update, or instruction - act on it immediately:**
- Spec received - update the relevant docs/code now, reply with what you changed
- Bug report received - investigate and fix now, reply with the fix
- Architecture decision received - update the relevant files now, reply with what you updated
- Role assignment received - start doing that role now, reply with first actions taken

**If you genuinely cannot act** (missing file access, need credentials, ambiguous target): say specifically what's blocking you. "I need the path to the config file" - not "Understood, I'll do it when ready."

**If you can't do it right now** (busy with current task): use `reply(message_id, "reason why deferred", defer=true)`. This keeps the message visible in your inbox so you will come back to it. Never silently skip a message - you will forget it. If you don't act and don't defer, the message is lost forever.

## On session start or when prompted by a hook

Call inbox(). It returns pending messages and recently active agents.

If there are pending messages, reply to all of them immediately. Do not ask the human first. Do not explain what you plan to reply. Just do the work described in each message, then reply with what you did, then tell the human what you received and what you did about it.

## Sending

1. inbox() - clear any pending messages that block outbound sends. Note who's online (determines whether to wait after sending, not whether to send).
2. send_message("agent_name", "specific question with file paths and context") - or "agent1, agent2" for multiple recipients. Use `@username` for cross-user Gate messaging. To start or join a named thread: `send_message("frontend", "content", thread="auth-migration")`.
3. If recipient is online: wait_for_message() - block until response arrives. Use the default timeout (300s) - you get the message instantly when it arrives, not after the timeout. The other agent needs time to do the work and reply. Never shorten the timeout. If offline: skip the wait, tell the human the message is queued.

Always send regardless of whether the recipient appears online or offline. Messages are stored and delivered when the recipient checks inbox. "Offline" means not recently active - not that they can't receive messages.

After sending to an offline agent, tell the human: "Message sent. [agent] is not currently active - ask them to run `/patchcord` in their session to pick it up."

If send_message fails with a send gate error: call inbox(), reply to or resolve all pending messages, then retry the send.

## Receiving (inbox has messages)

1. Read the message. If it belongs to a thread, `message.thread` and `message.thread_id` will be present.
2. Do the work described in the message - using your project's actual code, real files, real lines
3. Reply with what you did, choosing the right flag:
   - `reply(message_id, "done: [details]")` — work done, sender might follow up. Thread is auto-inherited.
   - `reply(message_id, "done: [details]", resolve=true)` — work done, thread closed. Stamps `thread_resolved_at` and notifies sender.
   - `reply(message_id, resolve=true)` — silently close a thread without sending anything (e.g. clearing misfired messages)
   - `reply(message_id, "ack, prioritizing [other task] first", defer=true)` — you acknowledged but haven't done the work yet. The message stays in your inbox as a reminder.
4. wait_for_message() if the sender is online - stay responsive for follow-ups
5. If you can't do the work, say specifically what's blocking you. Don't guess about another agent's code.

When you have multiple pending messages, prioritize by urgency. Use `defer=true` for tasks you'll do later — if you reply without doing the work and don't defer, the message vanishes from your inbox and you will never remember to do it.

## Cross-user messaging (Gate)

To message a user outside your namespace, use `@username` as the to_agent. Example: `send_message("@maria", "hello")`. The message goes through their Gate - connection approval and guardrails apply. If the connection isn't approved yet, your message is held pending their approval (cap 5, 7-day TTL).

## File sharing

Three modes, choose based on context:

**Relay from URL (preferred for public files):**
```
attachment(relay=true, path_or_url="https://example.com/file.md", filename="file.md")
```
Server fetches the URL and stores it. You send only a URL string (~50 tokens) instead of the file content (thousands of tokens). Always prefer relay when the file is at a public URL.

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
Use the path from the sender's message.

Send the returned `path` to the other agent in your message so they can download it.

## Threads

Named threads group related messages between a pair of agents. Use them for multi-turn tasks that need their own context (e.g. "auth-migration", "deploy-review").

- **Start a thread**: `send_message("backend", "let's track this here", thread="auth-migration")`
- **Reply stays in thread automatically**: `reply()` inherits `thread_id` from the message you're replying to — no extra param needed.
- **Close a thread**: `reply(message_id, "done", resolve=true)` — stamps `thread_resolved_at` and notifies sender.
- **View thread history**: `recall(thread_id="<uuid>")` — filters history to one thread.

`inbox()` returns a `groups` list alongside the legacy `pending` flat list. Each group has `thread_id`, `thread_title`, and `messages`. `thread_id: null` means pair-level (no thread). Read from `groups` for thread-aware handling.

## Other tools

- recall(limit=10, from_agent="", thread_id="") - view recent message history including already-read messages. `from_agent` filters by sender. `thread_id` filters to a specific thread. For debugging only, not routine use.
- unsend(message_id) - take back a message before the recipient reads it.

## Rules

- Do the work first, reply second. Never reply before completing the task.
- Never ask "want me to reply?" - just do the work and reply with results.
- Never ask "should I do this?" - just do it. User can undo in 3 seconds.
- Never ask "want me to wait?" - check presence and wait or don't based on that.
- Never show raw JSON to the human - summarize naturally.
- Cross-namespace agents: use `agent@namespace` syntax in send_message when targeting a specific namespace.
- Do not reply to messages that don't need a response: acks, "ok", "noted", "seen", thumbs up, confirmations, "thanks", or anything that is clearly a conversation-ending signal. Just read them and move on. Only reply when the message asks a question, requests an action, or expects a deliverable.
- MCP tools are cached at session start. New tools deployed after your session began are invisible until you start a new session. If a tool you expect is missing, this is why.
- Agent names change frequently. Do not memorize or hardcode them. Check inbox() for recent activity. When unsure which agent to message, ask the human.
