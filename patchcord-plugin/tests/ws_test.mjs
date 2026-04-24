// Smoke test for scripts/lib/ws.mjs. Run with:
//   node tests/ws_test.mjs
// Runs a fake WebSocket server, does round-trip + large-payload + bad-accept.
// Exits 0 on success, 1 on failure.

import { createServer } from "node:http";
import { createHash } from "node:crypto";
import { connect } from "../scripts/lib/ws.mjs";

const GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
let failed = 0;

function startEchoServer() {
  const server = createServer();
  server.on("upgrade", (req, socket) => {
    const key = req.headers["sec-websocket-key"];
    const accept = createHash("sha1").update(key + GUID).digest("base64");
    socket.write(
      [
        "HTTP/1.1 101 Switching Protocols",
        "Upgrade: websocket",
        "Connection: Upgrade",
        `Sec-WebSocket-Accept: ${accept}`,
        "",
        "",
      ].join("\r\n")
    );

    let buf = Buffer.alloc(0);
    socket.on("data", (chunk) => {
      buf = Buffer.concat([buf, chunk]);
      while (buf.length >= 2) {
        const b1 = buf[1];
        let len = b1 & 0x7f;
        let offset = 2;
        if (len === 126) {
          if (buf.length < offset + 2) return;
          len = buf.readUInt16BE(offset);
          offset += 2;
        }
        const masked = (b1 & 0x80) !== 0;
        let maskKey = null;
        if (masked) {
          if (buf.length < offset + 4) return;
          maskKey = buf.slice(offset, offset + 4);
          offset += 4;
        }
        if (buf.length < offset + len) return;
        let payload = buf.slice(offset, offset + len);
        if (maskKey) {
          const u = Buffer.alloc(len);
          for (let i = 0; i < len; i++) u[i] = payload[i] ^ maskKey[i % 4];
          payload = u;
        }
        const opcode = buf[0] & 0x0f;
        buf = buf.slice(offset + len);
        if (opcode === 0x1) {
          // echo back
          const msg = payload;
          let hdr;
          if (msg.length < 126) {
            hdr = Buffer.from([0x81, msg.length]);
          } else {
            hdr = Buffer.alloc(4);
            hdr[0] = 0x81;
            hdr[1] = 126;
            hdr.writeUInt16BE(msg.length, 2);
          }
          socket.write(Buffer.concat([hdr, msg]));
        } else if (opcode === 0x8) {
          socket.end();
          return;
        }
      }
    });
    socket.on("error", () => {});
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

async function testRoundTrip() {
  const server = await startEchoServer();
  const addr = server.address();
  const ws = connect(`ws://${addr.address}:${addr.port}/`);
  const result = await new Promise((res, rej) => {
    ws.on("open", () => ws.send("hello"));
    ws.on("message", (m) => {
      ws.close();
      res(m);
    });
    ws.on("error", rej);
  });
  server.close();
  if (result !== "hello") {
    console.error(`round-trip FAIL: expected "hello", got ${JSON.stringify(result)}`);
    failed++;
  } else {
    console.log("round-trip OK");
  }
}

async function testLargePayload() {
  const big = "x".repeat(500);
  const server = await startEchoServer();
  const addr = server.address();
  const ws = connect(`ws://${addr.address}:${addr.port}/`);
  const result = await new Promise((res, rej) => {
    ws.on("open", () => ws.send(big));
    ws.on("message", (m) => {
      ws.close();
      res(m);
    });
    ws.on("error", rej);
  });
  server.close();
  if (result !== big) {
    console.error(`large-payload FAIL: length ${result.length} vs ${big.length}`);
    failed++;
  } else {
    console.log("large-payload OK");
  }
}

async function testBadAccept() {
  const server = createServer();
  server.on("upgrade", (_req, socket) => {
    socket.write(
      [
        "HTTP/1.1 101 Switching Protocols",
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Accept: WRONG",
        "",
        "",
      ].join("\r\n")
    );
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const addr = server.address();
  const ws = connect(`ws://${addr.address}:${addr.port}/`);
  const err = await new Promise((res) => {
    ws.on("error", res);
  });
  server.close();
  if (!/Sec-WebSocket-Accept/.test(err.message)) {
    console.error(`bad-accept FAIL: ${err.message}`);
    failed++;
  } else {
    console.log("bad-accept OK");
  }
}

await testRoundTrip();
await testLargePayload();
await testBadAccept();

process.exit(failed === 0 ? 0 : 1);
