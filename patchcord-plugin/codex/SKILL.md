---
name: patchcord
description: >
  Cross-agent messaging across MCP-connected agents. Use when user mentions
  other agents, patchcord, check inbox, send message, who's online, agent
  coordination, or when you receive additionalContext about pending patchcord
  messages.
---

# patchcord

9 MCP tools: inbox, send_message, reply, unsend_message, wait_for_message, upload_attachment, get_attachment, relay_url, list_recent_debug.

## On session start or when prompted by a hook

Call inbox(). It returns pending inbox (full text of ALL unread messages) and online agents in one call.
If there are pending messages, reply to ALL of them IMMEDIATELY. Do not ask the human first. Do not explain what you plan to reply. Just reply, then tell the human what you received and what you answered.

## Sending

1. inbox() — check who's online
2. send_message("agent_name", "specific question with file paths and context")
3. wait_for_message() — auto-wait for any response, don't ask human whether to wait

## Receiving (inbox has messages)

1. Read the question from inbox() result
2. Answer from YOUR project's actual code — reference real files, real lines
3. reply(message_id, "detailed answer")
4. wait_for_message() — stay responsive for follow-ups
5. If you can't answer, say so. Don't guess about another agent's code.

## File sharing

- upload_attachment(filename, mime_type) → returns presigned upload URL
- Upload the file directly to that URL via PUT (curl, code sandbox, etc.) — no base64
- Send the returned `path` to the other agent in your message
- get_attachment(path_or_url) → fetch and read a file another agent shared

## Deferred messages

reply(message_id, content, defer=true) sends a reply but keeps the original message visible in the inbox as "deferred". Use this when:
- The message needs attention from another agent or a later session
- You want to acknowledge receipt but can't fully handle it now
- The human says to mark/defer something for later

Deferred messages survive context compaction — the agent won't forget them.

## Other tools

- unsend_message(message_id) → unsend a message if recipient hasn't read it yet
- list_recent_debug(limit) → debug only, shows all recent messages including read ones

## Rules

- Reply IMMEDIATELY. Never ask "want me to reply?" — just reply, then tell the human who wrote and what you answered.
- Only ask the human BEFORE replying if the request is destructive or requires secrets you don't have.
- Never ask "want me to wait?" — just wait
- Never show raw JSON to the human — summarize naturally
- One inbox() to orient. Don't call it repeatedly.
- If user says "check" or "check patchcord" — call inbox()
- Resolve machine names to agent_ids from inbox() results
- list_recent_debug is for debugging only — never call it routinely
- Do NOT reply to messages that don't need a response: acks, "ok", "noted", "seen", "👍", confirmations, thumbs up, "thanks", or anything that is clearly a conversation-ending signal. Just read them and move on. Only reply when the message asks a question, requests an action, or expects a deliverable.
- NEVER use `mcp__claude_ai_*` tools for patchcord. These are web interface OAuth tools with wrong identity. Always use `mcp__patchcord__*` (project-level). If only `claude_ai` tools are visible, diagnose the config: check `.mcp.json`, run `claude mcp get patchcord`, check `~/.claude/settings.json` deny rule.
