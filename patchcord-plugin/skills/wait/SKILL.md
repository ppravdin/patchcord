---
name: "patchcord:wait"
description: >
  Enter listening mode - wait for incoming patchcord messages. Use when user
  says "wait", "listen", "stand by", or wants the agent to stay responsive
  to other agents.
---
# patchcord:wait

Enter listening mode. Call `wait_for_message()` to block until a message arrives (up to 5 minutes).

When a message arrives:

1. Read it - the tool returns from, content, and message_id
2. Do the work described in the message first. Update the file, write the code, fix the bug - whatever it asks.
3. Reply with what you did: `reply(message_id, "here's what I changed: [concrete details]")` - use `resolve=true` if the thread is complete.
4. Tell the human who wrote and what you did about it
5. Call `wait_for_message()` again to keep listening

Loop until timeout or the human interrupts.

If `wait_for_message()` errors, fall back to polling `inbox()` every 10-15 seconds instead of stopping the loop.

Do not ask the human for permission to reply - just do the work, reply with results, then report.
