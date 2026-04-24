// Minimal WebSocket client (RFC 6455 subset).
// Zero dependencies, stdlib only — TLS + HTTP/1.1 Upgrade + text frames.
// Designed for Supabase Realtime: single-frame text messages, no extensions,
// no fragmentation, no binary.

import { connect as tlsConnect } from "node:tls";
import { connect as netConnect } from "node:net";
import { createHash, randomBytes } from "node:crypto";
import { EventEmitter } from "node:events";
import { URL } from "node:url";

const GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

export function connect(urlStr, { headers = {} } = {}) {
  const emitter = new EventEmitter();
  const url = new URL(urlStr);
  const isTls = url.protocol === "wss:";
  const port = url.port ? Number(url.port) : isTls ? 443 : 80;
  const key = randomBytes(16).toString("base64");
  const expectedAccept = createHash("sha1").update(key + GUID).digest("base64");

  const path = url.pathname + (url.search || "") || "/";
  const host = url.host;

  const reqHeaders = [
    `GET ${path} HTTP/1.1`,
    `Host: ${host}`,
    "Upgrade: websocket",
    "Connection: Upgrade",
    `Sec-WebSocket-Key: ${key}`,
    "Sec-WebSocket-Version: 13",
  ];
  for (const [k, v] of Object.entries(headers)) {
    reqHeaders.push(`${k}: ${v}`);
  }
  const req = reqHeaders.join("\r\n") + "\r\n\r\n";

  const socket = isTls
    ? tlsConnect({ host: url.hostname, port, servername: url.hostname })
    : netConnect({ host: url.hostname, port });

  let handshakeDone = false;
  let buf = Buffer.alloc(0);
  let closed = false;

  const close = (code = 1000, reason = "") => {
    if (closed) return;
    closed = true;
    try {
      socket.write(encodeFrame(0x8, closePayload(code, reason), true));
    } catch (_) {}
    socket.end();
  };

  const send = (str) => {
    if (closed) throw new Error("WebSocket closed");
    const payload = Buffer.from(str, "utf8");
    socket.write(encodeFrame(0x1, payload, true));
  };

  socket.on("error", (err) => {
    if (!closed) {
      closed = true;
      emitter.emit("error", err);
    }
  });

  socket.on("close", () => {
    if (!closed) {
      closed = true;
      emitter.emit("close", { code: null, reason: "socket-ended" });
    }
  });

  const onConnect = () => {
    socket.write(req);
  };
  if (isTls) socket.on("secureConnect", onConnect);
  else socket.on("connect", onConnect);

  socket.on("data", (chunk) => {
    buf = Buffer.concat([buf, chunk]);
    if (!handshakeDone) {
      const idx = buf.indexOf("\r\n\r\n");
      if (idx === -1) return;
      const header = buf.slice(0, idx).toString("utf8");
      buf = buf.slice(idx + 4);
      const lines = header.split("\r\n");
      const status = lines[0];
      if (!/^HTTP\/1\.1 101/.test(status)) {
        emitter.emit("error", new Error(`handshake failed: ${status}`));
        close();
        return;
      }
      const accept = lines
        .find((l) => /^sec-websocket-accept:/i.test(l))
        ?.split(":")[1]
        ?.trim();
      if (accept !== expectedAccept) {
        emitter.emit("error", new Error("invalid Sec-WebSocket-Accept"));
        close();
        return;
      }
      handshakeDone = true;
      emitter.emit("open");
    }

    // Parse as many frames as we have data for
    while (buf.length >= 2) {
      const parsed = decodeFrame(buf);
      if (parsed === null) break; // need more data
      const { opcode, payload, consumed } = parsed;
      buf = buf.slice(consumed);

      if (opcode === 0x1) {
        emitter.emit("message", payload.toString("utf8"));
      } else if (opcode === 0x8) {
        // close frame — parse code/reason for diagnostics
        let code = null;
        let reason = "";
        if (payload.length >= 2) {
          code = payload.readUInt16BE(0);
          if (payload.length > 2) {
            reason = payload.slice(2).toString("utf8");
          }
        }
        emitter.emit("close", { code, reason });
        closed = true;
        try {
          socket.write(encodeFrame(0x8, closePayload(1000, ""), true));
        } catch (_) {}
        socket.end();
        return;
      } else if (opcode === 0x9) {
        // ping → pong
        socket.write(encodeFrame(0xa, payload, true));
      } else if (opcode === 0xa) {
        // pong — ignore
      } else {
        // unsupported opcode (binary/continuation/extensions) → close
        emitter.emit("error", new Error(`unsupported opcode 0x${opcode.toString(16)}`));
        close(1003, "unsupported");
        return;
      }
    }
  });

  emitter.send = send;
  emitter.close = close;
  return emitter;
}

function encodeFrame(opcode, payload, mask) {
  const len = payload.length;
  let header;
  if (len < 126) {
    header = Buffer.from([0x80 | opcode, (mask ? 0x80 : 0) | len]);
  } else if (len < 65536) {
    header = Buffer.alloc(4);
    header[0] = 0x80 | opcode;
    header[1] = (mask ? 0x80 : 0) | 126;
    header.writeUInt16BE(len, 2);
  } else {
    header = Buffer.alloc(10);
    header[0] = 0x80 | opcode;
    header[1] = (mask ? 0x80 : 0) | 127;
    // Write big-endian 64-bit length. JS safe-integer limit (2^53) is well
    // above anything we'll ever send over this client.
    header.writeUInt32BE(0, 2);
    header.writeUInt32BE(len, 6);
  }

  if (!mask) return Buffer.concat([header, payload]);

  const maskKey = randomBytes(4);
  const masked = Buffer.alloc(len);
  for (let i = 0; i < len; i++) {
    masked[i] = payload[i] ^ maskKey[i % 4];
  }
  return Buffer.concat([header, maskKey, masked]);
}

function decodeFrame(buf) {
  if (buf.length < 2) return null;
  const b0 = buf[0];
  const b1 = buf[1];
  const fin = (b0 & 0x80) !== 0;
  const opcode = b0 & 0x0f;
  const masked = (b1 & 0x80) !== 0;
  let len = b1 & 0x7f;
  let offset = 2;

  if (len === 126) {
    if (buf.length < offset + 2) return null;
    len = buf.readUInt16BE(offset);
    offset += 2;
  } else if (len === 127) {
    if (buf.length < offset + 8) return null;
    const hi = buf.readUInt32BE(offset);
    const lo = buf.readUInt32BE(offset + 4);
    len = hi * 0x100000000 + lo;
    offset += 8;
  }

  let maskKey = null;
  if (masked) {
    if (buf.length < offset + 4) return null;
    maskKey = buf.slice(offset, offset + 4);
    offset += 4;
  }

  if (buf.length < offset + len) return null;

  let payload = buf.slice(offset, offset + len);
  if (maskKey) {
    const unmasked = Buffer.alloc(len);
    for (let i = 0; i < len; i++) {
      unmasked[i] = payload[i] ^ maskKey[i % 4];
    }
    payload = unmasked;
  }

  if (!fin) {
    // Reject fragmentation — Supabase Realtime never fragments.
    throw new Error("fragmented frames not supported");
  }

  return { opcode, payload, consumed: offset + len };
}

function closePayload(code, reason) {
  const reasonBuf = Buffer.from(reason, "utf8");
  const payload = Buffer.alloc(2 + reasonBuf.length);
  payload.writeUInt16BE(code, 0);
  reasonBuf.copy(payload, 2);
  return payload;
}
