#!/usr/bin/env node
// Patchcord subscribe: background listener that wakes Claude when new
// messages arrive for this agent. Connects to Supabase Realtime via
// WebSocket, prints one line to stdout per incoming INSERT event.
//
// Launched by the /patchcord:subscribe skill with run_in_background.
// Claude Code's Monitor tool watches our stdout and surfaces each
// "PATCHCORD: ..." line as a notification.

import { readFileSync, writeFileSync, unlinkSync, existsSync } from "node:fs";
import { request as httpsRequest } from "node:https";
import { request as httpRequest } from "node:http";
import { URL } from "node:url";
import { connect as wsConnect } from "./lib/ws.mjs";

const JWT_REFRESH_SAFETY_MARGIN_SEC = 120;
const HEARTBEAT_INTERVAL_MS = 25_000;
const RECONNECT_BACKOFF_MS = [1000, 2000, 4000, 8000, 15_000, 30_000];

// Guarantee a terminal stderr line on any unhandled failure so the agent
// reading Monitor's output file always sees WHY the process died.
process.on("uncaughtException", (err) => {
  process.stderr.write(`subscribe: fatal: uncaught: ${err?.stack || err?.message || err}\n`);
  process.exit(1);
});
process.on("unhandledRejection", (err) => {
  process.stderr.write(`subscribe: fatal: unhandled rejection: ${err?.stack || err?.message || err}\n`);
  process.exit(1);
});

function die(msg, code = 1) {
  process.stderr.write(msg + "\n");
  process.exit(code);
}

function readMcpConfig(cwd) {
  const path = `${cwd}/.mcp.json`;
  if (!existsSync(path)) die(`no .mcp.json in ${cwd}`);
  let json;
  try {
    json = JSON.parse(readFileSync(path, "utf8"));
  } catch (e) {
    die(`.mcp.json parse error: ${e.message}`);
  }
  const pc = json?.mcpServers?.patchcord;
  if (!pc?.url || !pc?.headers?.Authorization) {
    die(".mcp.json missing mcpServers.patchcord.url or Authorization");
  }
  let baseUrl = pc.url;
  // Strip known MCP path suffixes to get the API base
  baseUrl = baseUrl.replace(/\/mcp\/bearer$/, "").replace(/\/mcp$/, "");
  const auth = pc.headers.Authorization;
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : auth;
  return { baseUrl, token };
}

function httpJson(urlStr, { method = "GET", headers = {}, body = null } = {}) {
  return new Promise((resolve, reject) => {
    const url = new URL(urlStr);
    const lib = url.protocol === "https:" ? httpsRequest : httpRequest;
    const req = lib(
      {
        method,
        hostname: url.hostname,
        port: url.port || (url.protocol === "https:" ? 443 : 80),
        path: url.pathname + (url.search || ""),
        headers,
      },
      (res) => {
        let chunks = "";
        res.setEncoding("utf8");
        res.on("data", (c) => (chunks += c));
        res.on("end", () => resolve({ status: res.statusCode, body: chunks }));
      }
    );
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

async function fetchTicket(baseUrl, token) {
  const res = await httpJson(`${baseUrl}/api/realtime/ticket`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 401 || res.status === 403) {
    die(`ticket: token rejected (HTTP ${res.status}) — check .mcp.json`);
  }
  if (res.status === 501) {
    die("ticket: server not configured for realtime (self-hosted without Supabase?)");
  }
  if (res.status === 404) {
    die("ticket: namespace not owned — regenerate your token");
  }
  if (res.status !== 200) {
    throw new Error(`ticket HTTP ${res.status}: ${res.body.slice(0, 200)}`);
  }
  try {
    return JSON.parse(res.body);
  } catch (e) {
    throw new Error(`ticket: bad JSON: ${e.message}`);
  }
}

// Check if there are messages already pending in the inbox at the moment
// we connect (or reconnect). Realtime only delivers FUTURE INSERTs, so
// anything queued before we joined is invisible until the agent calls
// inbox() manually. Emit a stdout line when there's a pending queue so
// Monitor wakes the agent the same way a real arrival does.
async function drainQueueOnce(baseUrl, token) {
  const res = await httpJson(`${baseUrl}/api/inbox?count_only=1&limit=100`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status !== 200) {
    throw new Error(`inbox HTTP ${res.status}`);
  }
  let count = 0;
  try {
    count = JSON.parse(res.body).pending_count ?? 0;
  } catch (_) {}
  if (count > 0) {
    process.stdout.write(`PATCHCORD: ${count} waiting in inbox\n`);
  }
}

function writePidfile(path) {
  try {
    writeFileSync(path, String(process.pid), { flag: "wx" });
  } catch (e) {
    if (e.code === "EEXIST") {
      // Check if the PID is alive
      try {
        const existingPid = Number(readFileSync(path, "utf8").trim());
        if (existingPid && existingPid !== process.pid) {
          try {
            process.kill(existingPid, 0);
            die(`already running (pid ${existingPid})`, 2);
          } catch (_) {
            // stale
            try {
              unlinkSync(path);
            } catch (_) {}
            writeFileSync(path, String(process.pid), { flag: "wx" });
            return;
          }
        }
      } catch (_) {
        try {
          unlinkSync(path);
        } catch (_) {}
        writeFileSync(path, String(process.pid), { flag: "wx" });
        return;
      }
    } else {
      throw e;
    }
  }
}

function removePidfile(path) {
  try {
    unlinkSync(path);
  } catch (_) {}
}

async function run() {
  const cwd = process.cwd();
  const { baseUrl, token } = readMcpConfig(cwd);
  process.stderr.write(`subscribe: cwd=${cwd} server=${baseUrl}\n`);

  let ticket = await fetchTicket(baseUrl, token);
  const pidfile = `/tmp/patchcord_subscribe_${ticket.namespace_ids[0]}_${ticket.agent_id}.pid`;
  writePidfile(pidfile);

  const cleanup = () => removePidfile(pidfile);
  process.on("exit", cleanup);
  process.on("SIGINT", () => {
    cleanup();
    process.exit(0);
  });
  process.on("SIGTERM", () => {
    cleanup();
    process.exit(0);
  });

  process.stderr.write(
    `subscribe: agent=${ticket.agent_id} namespaces=${ticket.namespace_ids.join(",")}\n`
  );

  let backoffIdx = 0;

  const loop = async () => {
    while (true) {
      try {
        await runOnce(ticket, baseUrl, token, async () => {
          ticket = await fetchTicket(baseUrl, token);
          return ticket;
        });
        backoffIdx = 0; // clean disconnect resets backoff
      } catch (e) {
        process.stderr.write(`subscribe: ${e.message}\n`);
      }
      const delay = RECONNECT_BACKOFF_MS[Math.min(backoffIdx, RECONNECT_BACKOFF_MS.length - 1)];
      backoffIdx++;
      process.stderr.write(`subscribe: reconnecting in ${delay}ms\n`);
      await new Promise((r) => setTimeout(r, delay));
      try {
        ticket = await fetchTicket(baseUrl, token);
      } catch (e) {
        process.stderr.write(`subscribe: ticket refresh failed: ${e.message}\n`);
      }
    }
  };

  await loop();
}

function runOnce(ticket, baseUrl, token, refreshTicket) {
  return new Promise((resolve, reject) => {
    const allowedNs = new Set(ticket.namespace_ids);
    const wsUrl = `${ticket.realtime_url}?apikey=${encodeURIComponent(ticket.apikey)}&vsn=1.0.0`;
    const ws = wsConnect(wsUrl);

    let ref = 1;
    let heartbeatTimer = null;
    let refreshTimer = null;
    let currentJwt = ticket.jwt;
    let settled = false;

    const done = (err) => {
      if (settled) return;
      settled = true;
      if (heartbeatTimer) clearInterval(heartbeatTimer);
      if (refreshTimer) clearTimeout(refreshTimer);
      try {
        ws.close();
      } catch (_) {}
      if (err) reject(err);
      else resolve();
    };

    ws.on("open", () => {
      process.stderr.write("subscribe: connected\n");
      for (const topic of ticket.topics) {
        ws.send(
          JSON.stringify({
            topic: topic.name,
            event: "phx_join",
            payload: {
              config: topic.config,
              access_token: currentJwt,
            },
            ref: String(ref++),
          })
        );
      }

      // Drain any messages already in the queue when we connected.
      // Realtime only delivers FUTURE INSERTs — anything pending before
      // we joined (or that arrived during a reconnect gap) wouldn't
      // otherwise wake the agent. Fire-and-forget: a transient HTTP
      // failure here just means we miss queued messages this round;
      // the next reconnect retries.
      drainQueueOnce(baseUrl, token).catch((e) => {
        process.stderr.write(`subscribe: queue check failed: ${e.message}\n`);
      });
      heartbeatTimer = setInterval(() => {
        try {
          ws.send(
            JSON.stringify({
              topic: "phoenix",
              event: "heartbeat",
              payload: {},
              ref: String(ref++),
            })
          );
        } catch (_) {}
      }, HEARTBEAT_INTERVAL_MS);

      const scheduleRefresh = (ttlSec) => {
        const refreshIn = Math.max((ttlSec - JWT_REFRESH_SAFETY_MARGIN_SEC) * 1000, 30_000);
        refreshTimer = setTimeout(doRefresh, refreshIn);
      };

      const doRefresh = async () => {
        if (settled) return;
        try {
          const fresh = await refreshTicket();
          currentJwt = fresh.jwt;
          // Socket-level auth update (phoenix topic) — what Supabase
          // actually uses for the connection's own JWT expiry check.
          // Without this, the server closes the socket at the original
          // JWT's exp regardless of per-channel updates.
          ws.send(
            JSON.stringify({
              topic: "phoenix",
              event: "access_token",
              payload: { access_token: currentJwt },
              ref: String(ref++),
            })
          );
          // Channel-level updates — matches supabase-js's setAuth() pattern.
          for (const topic of fresh.topics) {
            ws.send(
              JSON.stringify({
                topic: topic.name,
                event: "access_token",
                payload: { access_token: currentJwt },
                ref: String(ref++),
              })
            );
          }
          process.stderr.write("subscribe: token refreshed\n");
          scheduleRefresh(fresh.jwt_expires_in);
        } catch (e) {
          // Transient network/server error — do NOT close the live
          // connection. The current JWT is still valid for ~2 more min
          // (JWT_REFRESH_SAFETY_MARGIN_SEC). Retry sooner.
          process.stderr.write(`subscribe: token refresh failed, retrying in 30s: ${e.message}\n`);
          refreshTimer = setTimeout(doRefresh, 30_000);
        }
      };

      scheduleRefresh(ticket.jwt_expires_in);
    });

    ws.on("message", (raw) => {
      let frame;
      try {
        frame = JSON.parse(raw);
      } catch {
        return;
      }
      if (frame.event !== "postgres_changes") return;
      const data = frame.payload?.data;
      if (!data || data.type !== "INSERT") return;
      const rec = data.record;
      if (!rec) return;
      // Defense-in-depth: verify the row's namespace_id is in our scope.
      // RLS already enforces this server-side, but if policies drift we
      // don't want to leak cross-namespace notifications.
      if (rec.namespace_id && !allowedNs.has(rec.namespace_id)) return;
      const from = rec.from_agent || "unknown";
      process.stdout.write(`PATCHCORD: 1 new from ${from}\n`);
    });

    ws.on("error", (err) => {
      process.stderr.write(`subscribe: ws error: ${err.message}\n`);
      done(err);
    });

    ws.on("close", (info) => {
      const codeStr = info?.code != null ? `code=${info.code}` : "code=none";
      const reasonStr = info?.reason ? ` reason=${JSON.stringify(info.reason)}` : "";
      process.stderr.write(`subscribe: ws closed (${codeStr}${reasonStr})\n`);
      done();
    });
  });
}

run().catch((e) => {
  process.stderr.write(`subscribe: fatal: ${e.message}\n`);
  process.exit(1);
});
