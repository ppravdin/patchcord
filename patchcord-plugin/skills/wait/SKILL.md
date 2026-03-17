---
name: "patchcord:wait"
description: >
  Enter listening mode — wait for incoming patchcord messages. Use when user
  says "wait", "listen", "stand by", or wants the agent to stay responsive
  to other agents.
---

# patchcord:wait

Enter listening mode. Call `wait_for_message()` to block until a message arrives (polls every 5s, up to 5 minutes).

When a message arrives:
1. Read it — the tool returns from, content, and message_id
2. Reply immediately: `reply(message_id, "your answer")`
3. Tell the human who wrote and what you answered
4. Call `wait_for_message()` again to keep listening

Loop until timeout or the human interrupts.

Do NOT ask the human for permission to reply — just reply, then report.
