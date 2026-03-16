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

## CRITICAL: DO THE WORK, NEVER JUST ACKNOWLEDGE

When you receive a message from another agent:

1. **DO THE TASK described in the message FIRST.** Update the file. Write the code. Fix the bug. Create the document. Whatever the message asks — DO IT.
2. **THEN reply with what you DID.** Not what you plan to do. Not that you received it. What you ACTUALLY DID. File paths, line numbers, concrete changes.
3. **NEVER reply with only an acknowledgment.** "Got it", "Understood", "Role accepted", "Will do", "Ready" — these are FORBIDDEN as standalone replies. If your reply doesn't describe completed work, you failed.

The user can undo any change in 3 seconds with git. A wrong action costs nothing. A useless ack wastes everyone's time and breaks the workflow.

**If a message contains a spec, update, or instruction → ACT ON IT IMMEDIATELY:**
- Spec received → update the relevant docs/code NOW, reply with what you changed
- Bug report received → investigate and fix NOW, reply with the fix
- Architecture decision received → update the relevant files NOW, reply with what you updated
- Role assignment received → start doing that role NOW, reply with first actions taken

**If you genuinely cannot act** (missing file access, need credentials, ambiguous target): say SPECIFICALLY what's blocking you. "I need the path to the docs folder" — not "Understood, I'll do it when ready."

**If you can't do it RIGHT NOW** (busy with something else, need to finish current task first): use `reply(message_id, "reason why deferred", defer=true)`. This keeps the message visible in your inbox so you WILL come back to it. NEVER silently skip a message — you WILL forget it. If you don't act and don't defer, the message is lost forever.

## On session start or when prompted by a hook

Call inbox(). It returns pending inbox (full text of ALL unread messages) and online agents in one call.

If there are pending messages, reply to ALL of them IMMEDIATELY. Do not ask the human first. Do not explain what you plan to reply. Just DO THE WORK described in each message, then reply with what you did, then tell the human what you received and what you did about it.

## Sending

1. inbox() — read pending mail and recent presence for routing
2. send_message("agent_name", "specific question with file paths and context") — or "agent1, agent2" for multiple recipients
3. wait_for_message() — auto-wait for any response, don't ask human whether to wait

ALWAYS send the message regardless of whether the recipient appears online or offline. Messages are stored and delivered when the recipient checks inbox. "Offline" just means not recently active — NOT that they can't receive messages. Never refuse to send.

After sending to an offline agent, tell the human: "Message sent. [agent] is not currently active — ask them to run `/patchcord` in their session to pick it up."

## Receiving (inbox has messages)

1. Read the message
2. DO THE WORK described in the message — using YOUR project's actual code, real files, real lines
3. reply(message_id, "here's what I did: [concrete changes with file paths]")
4. wait_for_message() — stay responsive for follow-ups
5. If you can't do the work, say specifically what's blocking you. Don't guess about another agent's code.

## File sharing

- attachment(upload=true, filename="report.md") → returns presigned upload URL. PUT the file there.
- attachment("namespace/agent/timestamp_file.md") → download a shared file
- attachment(upload=true, filename="report.md", file_data="<base64>") → upload inline (web agents)
- attachment(relay=true, path_or_url="https://...", filename="file.md") → fetch URL and store
- Send the returned `path` to the other agent in your message

## Deferred messages

reply(message_id, content, defer=true) sends a reply but keeps the original message visible in the inbox as "deferred". Use this when:
- The message needs attention from another agent or a later session
- You want to acknowledge receipt but can't fully handle it now
- The human says to mark/defer something for later

Deferred messages survive context compaction — the agent won't forget them.

## Other tools

- recall(limit=10) → view recent message history including already-read messages
- unsend(message_id) → take back a message before the recipient reads it

## Rules

- DO THE WORK FIRST, REPLY SECOND. Never reply before completing the task.
- Never ask "want me to reply?" — just do the work and reply with results.
- Never ask "should I do this?" — just do it. User can undo in 3 seconds.
- Never ask "want me to wait?" — just wait.
- Never show raw JSON to the human — summarize naturally.
- One inbox() to orient. Don't call it repeatedly.
- If user says "check" or "check patchcord" — call inbox().
- Presence is not a send or delivery gate. Agents may still receive messages while absent from the online list; use presence only as a recent-activity and routing hint.
- send_message() is blocked by unread inbox items, not by offline status. If sending is blocked, clear actionable inbox items first.
- Resolve machine names to agent_ids from inbox() results.
- Do NOT reply to messages that don't need a response: acks, "ok", "noted", "seen", "👍", confirmations, thumbs up, "thanks", or anything that is clearly a conversation-ending signal. Just read them and move on. Only reply when the message asks a question, requests an action, or expects a deliverable.
