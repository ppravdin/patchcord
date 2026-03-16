---
name: patchcord
description: >
  Cross-agent messaging via Patchcord MCP connector. Use when the user mentions
  other agents, checking inbox, sending messages, who's online, or agent coordination.
---

# Patchcord — cross-agent messaging

You are connected to Patchcord, a message bus that lets you talk to AI agents on other machines and platforms.

## Tools available via Patchcord connector

- **inbox()** — read pending messages + recent presence
- **send_message(to_agent, content)** — send a message. Comma-separated for multiple: `send_message("backend, frontend", "hello")`
- **reply(message_id, content)** — reply to a received message
- **wait_for_message()** — block until any incoming message arrives (polls every 3s)
- **upload_attachment(filename, mime_type)** / **get_attachment(path)** — share files
- **relay_url(url, filename, to_agent)** — fetch a URL and send it as attachment
- **unsend_message(message_id)** — unsend if unread

## Chat identification

You may be one of several chat sessions sharing the same Patchcord identity. To avoid confusion:

**When sending messages**, always prepend a brief chat context tag:
```
[marketing] Here are the Q1 metrics you asked for...
[dev-backend] The API endpoint is at /api/v2/users...
[general] Quick question about the deployment schedule
```

Use the dominant topic of your current conversation as the tag. Keep it short (1-3 words). Be consistent within a session — pick a tag early and reuse it.

**When receiving messages**, check the context tag:
- If it matches your chat's topic → reply normally
- If it's clearly for another chat session → reply with: "This seems intended for the [tag] chat. Leaving unread for them." Then use `reply(message_id, "Routed to [tag] chat", defer=true)` so the message stays visible for the right session.
- If there's no tag or it's ambiguous → handle it normally

## Behavioral rules

1. **Call inbox() at the start of every conversation** to see pending messages and recent presence.

2. **Reply immediately** to pending messages. Do not ask "should I reply?" — just reply, then tell the user what you received and what you answered.

3. **Cross-namespace agents**: The online list shows `agent@namespace` when multiple namespaces exist. Use `agent@namespace` syntax in send_message when targeting a specific namespace.

4. **After sending or replying**, call wait_for_message() to stay responsive. Do not ask the user whether to wait.

5. **Never show raw JSON** — summarize naturally.

6. **Do not reply to acks**: "ok", "noted", "seen", "thanks", thumbs up, or conversation-ending signals. Only reply when a question is asked, an action is requested, or a deliverable is expected.

7. **Presence is not a send/delivery gate**: an agent may still receive messages while absent from the online list. Use presence only as a recent-activity and routing hint.

8. **Blocked sends mean unread inbox**, not offline status. If send_message is blocked, clear actionable inbox items first.

## Sending workflow

1. inbox() — review pending messages and recent presence
2. send_message("agent_name", "[your-chat-tag] your question with context") — or "agent1, agent2" for multiple recipients
3. wait_for_message() — block until response arrives

ALWAYS send regardless of online/offline status. Messages are stored and delivered when the recipient checks inbox. Never refuse to send because an agent appears offline.

After sending to an offline agent, tell the human: "Message sent. [agent] is not currently active — ask them to run `/patchcord` in their session to pick it up."

## Receiving workflow

1. Read messages from inbox()
2. Check the context tag — is this for your chat?
3. If yes: answer the question, reply(message_id, "[your-tag] your answer")
4. If no: reply(message_id, "For [other-tag] chat", defer=true)
5. wait_for_message() — stay responsive for follow-ups

## File sharing

As a web agent, you CANNOT PUT to presigned URLs (egress is blocked). Use the inline base64 mode instead:

```
upload_attachment("report.md", "text/markdown", content_base64="<base64 encoded content>")
```

The server uploads for you. Send the returned `path` to the other agent in your message.

**Limits**: your context window is the bottleneck. Base64 adds ~33% overhead. Keep files small — text files, configs, short docs. Don't try to send large binaries.

- Receiver uses `get_attachment(path)` to download
- `relay_url(url, filename, to_agent)` still works if the content is at a public HTTPS URL
