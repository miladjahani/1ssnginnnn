/**
 * Local verification of the Cloudflare gateway worker (node --test).
 *
 * Node exposes the same Request/Response/Headers/fetch globals as Workers, so the
 * gateway logic can be exercised against a real local origin without a Cloudflare
 * account: `node --test scripts/test_gateway_worker.mjs`
 */
import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { once } from "node:events";

import worker from "../_worker.js";

async function startOrigin() {
  const requests = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      requests.push({ method: req.method, url: req.url, headers: req.headers, body });
      if (req.url.startsWith("/login-redirect")) {
        res.writeHead(302, { Location: `https://${req.headers.host}/login` });
        res.end();
        return;
      }
      if (req.url.startsWith("/api/auth/login")) {
        res.writeHead(200, { "content-type": "application/json", "set-cookie": "miliconfig_token=abc; HttpOnly" });
        res.end(JSON.stringify({ ok: true }));
        return;
      }
      res.writeHead(200, { "content-type": "text/plain" });
      res.end("origin-ok");
    });
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return { server, port: server.address().port, requests };
}

const clientHost = "panel.example.ir";

test("gateway proxies HTTP, rewrites redirects and forwards WebSocket upgrades", async (t) => {
  const { server, port, requests } = await startOrigin();
  const env = { BACKEND_URL: `http://127.0.0.1:${port}` };
  t.after(() => server.close());

  await t.test("GET keeps path/query and reports the client host", async () => {
    const res = await worker.fetch(
      new Request(`https://${clientHost}/sub/token123?target=clash`, { headers: { "CF-Connecting-IP": "5.6.7.8" } }),
      env
    );
    assert.equal(res.status, 200);
    assert.equal(await res.text(), "origin-ok");
    assert.equal(res.headers.get("X-MILICONFIG-Gateway"), "cloudflare");
    const seen = requests.at(-1);
    assert.equal(seen.url, "/sub/token123?target=clash");
    assert.equal(seen.headers["x-forwarded-host"], clientHost);
    assert.equal(seen.headers["x-forwarded-proto"], "https");
    assert.equal(seen.headers["x-real-ip"], "5.6.7.8");
    assert.equal(seen.headers["cf-ray"], undefined, "Cloudflare headers must not leak to the origin");
  });

  await t.test("POST body and content type survive proxying", async () => {
    const res = await worker.fetch(
      new Request(`https://${clientHost}/api/auth/login`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ username: "admin", password: "secret" }),
      }),
      env
    );
    assert.equal(res.status, 200);
    assert.equal(res.headers.get("set-cookie"), "miliconfig_token=abc; HttpOnly");
    const seen = requests.at(-1);
    assert.equal(seen.method, "POST");
    assert.equal(seen.body, '{"username":"admin","password":"secret"}');
    assert.ok(seen.headers["content-length"] === undefined || seen.headers["content-length"] === "36");
  });

  await t.test("redirect Location points back at the unblocked domain", async () => {
    const res = await worker.fetch(new Request(`https://${clientHost}/login-redirect`), env);
    assert.equal(res.status, 302);
    assert.equal(res.headers.get("Location"), `https://${clientHost}/login`);
  });

  await t.test("WebSocket upgrade handshake headers reach the origin", async () => {
    // A real 101 tunnel can only be completed on Cloudflare's edge, so this stub
    // asserts the gateway forwards the handshake (upgrade/connection/key) plus the
    // client host, and does not attach a request body.
    const originalFetch = globalThis.fetch;
    let captured = null;
    globalThis.fetch = async (url, init) => {
      captured = { url, init };
      return new Response("ws-tunnel", { status: 200 });
    };
    try {
      const res = await worker.fetch(
        new Request(`https://${clientHost}/?ed=2048`, {
          headers: {
            Upgrade: "websocket",
            Connection: "Upgrade",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
            "Sec-WebSocket-Version": "13",
          },
        }),
        env
      );
      assert.equal(res.status, 200);
      assert.equal(await res.text(), "ws-tunnel");
      assert.equal(captured.url, `http://127.0.0.1:${port}/?ed=2048`);
      assert.equal(captured.init.method, "GET");
      assert.equal(captured.init.body, undefined, "WebSocket upgrades must not carry a body");
      const sent = captured.init.headers;
      assert.equal(sent.get("Upgrade"), "websocket");
      assert.match(sent.get("Connection"), /upgrade/i);
      assert.equal(sent.get("Sec-WebSocket-Key"), "dGhlIHNhbXBsZSBub25jZQ==");
      assert.equal(sent.get("X-Forwarded-Host"), clientHost);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

});
