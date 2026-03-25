#!/usr/bin/env bun
/**
 * Patchcord channel for Claude Code.
 *
 * Polls the Patchcord server for new messages and pushes them as native
 * <channel> notifications. Exposes reply and send_message tools for
 * two-way communication.
 *
 * Config: PATCHCORD_TOKEN and PATCHCORD_SERVER env vars (set by .mcp.json).
 */

import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from '@modelcontextprotocol/sdk/types.js'

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const TOKEN = process.env.PATCHCORD_TOKEN ?? ''
const SERVER = (process.env.PATCHCORD_SERVER ?? 'https://mcp.patchcord.dev').replace(/\/+$/, '')
const POLL_INTERVAL_MS = 3000

if (!TOKEN) {
  process.stderr.write(
    `patchcord channel: PATCHCORD_TOKEN required\n` +
    `  Set it in .mcp.json env block or as a shell environment variable.\n`,
  )
  process.exit(1)
}

// Safety nets
process.on('unhandledRejection', err => {
  process.stderr.write(`patchcord channel: unhandled rejection: ${err}\n`)
})
process.on('uncaughtException', err => {
  process.stderr.write(`patchcord channel: uncaught exception: ${err}\n`)
})

// ---------------------------------------------------------------------------
// Identity (resolved on startup)
// ---------------------------------------------------------------------------

let agentId = ''
let namespaceId = ''

async function resolveIdentity(): Promise<void> {
  const res = await fetch(`${SERVER}/api/inbox?limit=0&count_only=true`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`identity check failed: ${res.status} ${text.slice(0, 200)}`)
  }
  const data = await res.json() as { agent_id: string; namespace_id: string }
  agentId = data.agent_id
  namespaceId = data.namespace_id
  process.stderr.write(`patchcord channel: connected as ${agentId}@${namespaceId}\n`)
}

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------

const headers = {
  Authorization: `Bearer ${TOKEN}`,
  'Content-Type': 'application/json',
}

async function channelPoll(): Promise<PollMessage[]> {
  const res = await fetch(`${SERVER}/api/channel/poll`, {
    method: 'POST',
    headers,
    body: JSON.stringify({}),
  })
  if (!res.ok) {
    throw new Error(`poll failed: ${res.status}`)
  }
  return await res.json() as PollMessage[]
}

async function channelSend(to_agent: string, content: string): Promise<any> {
  const res = await fetch(`${SERVER}/api/channel/send`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ to_agent, content }),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`send failed: ${res.status} ${text.slice(0, 200)}`)
  }
  return await res.json()
}

async function channelReply(message_id: string, content: string): Promise<any> {
  const res = await fetch(`${SERVER}/api/channel/reply`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ message_id, content }),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`reply failed: ${res.status} ${text.slice(0, 200)}`)
  }
  return await res.json()
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type PollMessage = {
  id: string
  from_agent: string
  content: string
  created_at: string
  namespace_id: string
  reply_to: string | null
}

// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------

const mcp = new Server(
  { name: 'patchcord', version: '0.1.0' },
  {
    capabilities: {
      experimental: { 'claude/channel': {} },
      tools: {},
    },
    instructions: [
      'Messages from Patchcord agents arrive as <channel source="patchcord" from="..." message_id="..." namespace="...">.',
      'Reply with the reply tool, passing message_id from the notification.',
      'Use send_message to start new conversations with other agents.',
      'Messages arrive automatically - no need to call inbox or wait_for_message.',
    ].join(' '),
  },
)

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------

mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: 'reply',
      description: 'Reply to a Patchcord message. Pass message_id from the <channel> notification.',
      inputSchema: {
        type: 'object' as const,
        properties: {
          message_id: { type: 'string', description: 'ID from the <channel> notification' },
          content: { type: 'string', description: 'Reply text (up to 50,000 characters)' },
        },
        required: ['message_id', 'content'],
      },
    },
    {
      name: 'send_message',
      description: 'Send a new message to a Patchcord agent. Use commas for multiple recipients.',
      inputSchema: {
        type: 'object' as const,
        properties: {
          to_agent: { type: 'string', description: 'Target agent name, optionally with @namespace' },
          content: { type: 'string', description: 'Message text (up to 50,000 characters)' },
        },
        required: ['to_agent', 'content'],
      },
    },
    {
      name: 'inbox',
      description: 'Check current Patchcord inbox. Shows pending messages. Normally not needed - messages arrive as push notifications.',
      inputSchema: {
        type: 'object' as const,
        properties: {},
      },
    },
  ],
}))

mcp.setRequestHandler(CallToolRequestSchema, async req => {
  const { name } = req.params
  const args = req.params.arguments as Record<string, string>

  if (name === 'reply') {
    if (!args.message_id || !args.content) {
      return { content: [{ type: 'text', text: 'Error: message_id and content are required' }] }
    }
    try {
      const result = await channelReply(args.message_id, args.content)
      return { content: [{ type: 'text', text: `Replied to ${result.to_agent} [${result.id}]` }] }
    } catch (err) {
      return { content: [{ type: 'text', text: `Error: ${err}` }] }
    }
  }

  if (name === 'send_message') {
    if (!args.to_agent || !args.content) {
      return { content: [{ type: 'text', text: 'Error: to_agent and content are required' }] }
    }
    try {
      const result = await channelSend(args.to_agent, args.content)
      return { content: [{ type: 'text', text: `Sent to ${result.to_agent} [${result.id}]` }] }
    } catch (err) {
      return { content: [{ type: 'text', text: `Error: ${err}` }] }
    }
  }

  if (name === 'inbox') {
    try {
      const messages = await channelPoll()
      if (messages.length === 0) {
        return { content: [{ type: 'text', text: `${agentId}@${namespaceId} | 0 pending` }] }
      }
      const lines = [`${agentId}@${namespaceId} | ${messages.length} pending`]
      for (const msg of messages) {
        lines.push('')
        lines.push(`From ${msg.from_agent} [${msg.id}]`)
        lines.push(`  ${msg.content}`)
        // Push as notification too
        await pushMessage(msg)
      }
      return { content: [{ type: 'text', text: lines.join('\n') }] }
    } catch (err) {
      return { content: [{ type: 'text', text: `Error: ${err}` }] }
    }
  }

  throw new Error(`unknown tool: ${name}`)
})

// ---------------------------------------------------------------------------
// Poll loop
// ---------------------------------------------------------------------------

let consecutiveFailures = 0
let connectionLostNotified = false

async function pushMessage(msg: PollMessage): Promise<void> {
  const meta: Record<string, string> = {
    from: msg.from_agent,
    message_id: msg.id,
    namespace: msg.namespace_id,
    sent_at: msg.created_at,
  }
  if (msg.reply_to) {
    meta.in_reply_to = msg.reply_to
  }
  await mcp.notification({
    method: 'notifications/claude/channel',
    params: { content: msg.content, meta },
  })
}

async function poll(): Promise<void> {
  try {
    const messages = await channelPoll()
    consecutiveFailures = 0
    if (connectionLostNotified) {
      connectionLostNotified = false
      await mcp.notification({
        method: 'notifications/claude/channel',
        params: {
          content: 'Patchcord connection restored.',
          meta: { from: 'system', message_id: 'system' },
        },
      })
    }
    for (const msg of messages) {
      await pushMessage(msg)
    }
  } catch (err) {
    consecutiveFailures++
    process.stderr.write(`patchcord channel: poll error (${consecutiveFailures}): ${err}\n`)

    if (consecutiveFailures === 1) {
      // Check if it's an auth error
      const errStr = String(err)
      if (errStr.includes('401')) {
        process.stderr.write('patchcord channel: auth failed, stopping poll\n')
        await mcp.notification({
          method: 'notifications/claude/channel',
          params: {
            content: 'Patchcord auth failed. Check your token configuration.',
            meta: { from: 'system', message_id: 'system' },
          },
        })
        clearInterval(pollTimer)
        return
      }
    }

    if (consecutiveFailures >= 10 && !connectionLostNotified) {
      connectionLostNotified = true
      try {
        await mcp.notification({
          method: 'notifications/claude/channel',
          params: {
            content: 'Patchcord connection lost. Retrying...',
            meta: { from: 'system', message_id: 'system' },
          },
        })
      } catch {
        // notification itself failed, give up
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------

// Resolve identity first
try {
  await resolveIdentity()
} catch (err) {
  process.stderr.write(`patchcord channel: failed to connect: ${err}\n`)
  process.stderr.write('patchcord channel: will retry on first poll\n')
}

// Connect MCP over stdio
await mcp.connect(new StdioServerTransport())

// Drain existing messages, then start polling
await poll()
const pollTimer = setInterval(poll, POLL_INTERVAL_MS)
