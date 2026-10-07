/**
 * MILICONFIG — Cloudflare Unblocked Gateway
 *
 * Serves the admin panel, subscriptions AND VLESS/Trojan WebSockets from a
 * Cloudflare domain (custom domain, *.workers.dev or *.pages.dev) while the real
 * application keeps running on Railway. Use this when the Railway hostname
 * (*.up.railway.app) is filtered by your ISP.
 *
 * Configuration (env):
 *   BACKEND_URL   full origin of the Railway service, e.g. https://your-app.up.railway.app
 *                 (optional; BACKEND_HOST builds an https origin automatically)
 *   BACKEND_HOST  Railway hostname, e.g. your-app.up.railway.app
 *
 * Deployment
 *  1) Worker:    `npx wrangler deploy` (see wrangler.toml) then attach a custom domain
 *  2) Pages:     put this file at the root of a Pages project (advanced mode)
 *  3) Dashboard: Workers & Pages -> Create Worker -> paste this file and set the variables
 */

const DEFAULT_BACKEND = "milinewc2-production.up.railway.app";

// Cloudflare request headers that must not be forwarded to the origin verbatim.
const STRIP_HEADERS = [
  "host",
  "cf-connecting-ip",
  "cf-connecting-ipv6",
  "cf-ipcountry",
  "cf-ray",
  "cf-visitor",
  "cf-worker",
  "true-client-ip",
  "x-real-ip",
  "content-length",
];

function resolveBackend(env) {
  const raw = (env && (env.BACKEND_URL || env.BACKEND_HOST)) || DEFAULT_BACKEND;
  const withScheme = /^[a-z]+:\/\//i.test(raw) ? raw : `https://${raw}`;
  try {
    return new URL(withScheme);
  } catch (e) {
    return null;
  }
}

function isWebSocketUpgrade(request) {
  const upgrade = (request.headers.get("Upgrade") || "").toLowerCase();
  const connection = (request.headers.get("Connection") || "").toLowerCase();
  return upgrade === "websocket" || connection.includes("upgrade");
}

export default {
  async fetch(request, env) {
    const backend = resolveBackend(env);
    if (!backend) {
      return new Response(
        "MILICONFIG gateway misconfigured: set BACKEND_URL (or BACKEND_HOST) to your Railway origin.",
        { status: 500, headers: { "content-type": "text/plain; charset=utf-8" } }
      );
    }

    const clientUrl = new URL(request.url);
    const targetUrl = new URL(request.url);
    targetUrl.protocol = backend.protocol;
    targetUrl.hostname = backend.hostname;
    targetUrl.port = backend.port;

    const headers = new Headers(request.headers);
    for (const name of STRIP_HEADERS) {
      headers.delete(name);
    }
    headers.set("X-Forwarded-Host", clientUrl.host);
    headers.set("X-Forwarded-Proto", clientUrl.protocol.replace(":", ""));
    const clientIp = request.headers.get("CF-Connecting-IP");
    if (clientIp) {
      headers.set("X-Real-IP", clientIp);
    }

    // --- WebSocket tunnel (VLESS / Trojan over WS) ---
    // A WebSocket upgrade is forwarded with all handshake headers so Cloudflare can
    // complete the 101 handshake with the client and pipe frames to Railway.
    if (isWebSocketUpgrade(request)) {
      return fetch(targetUrl.toString(), {
        method: request.method,
        headers: headers,
        redirect: "manual",
      });
    }

    const init = {
      method: request.method,
      headers: headers,
      redirect: "manual",
    };
    if (request.method !== "GET" && request.method !== "HEAD") {
      init.body = request.body;
      // Required when streaming a request body in Node-compatible runtimes; a no-op
      // on Cloudflare Workers.
      init.duplex = "half";
    }

    const response = await fetch(targetUrl.toString(), init);
    const newHeaders = new Headers(response.headers);

    // Keep the visitor on the unblocked domain when the backend redirects.
    const location = response.headers.get("Location");
    if (location) {
      try {
        const locUrl = new URL(location, request.url);
        if (locUrl.hostname === backend.hostname) {
          locUrl.protocol = clientUrl.protocol;
          locUrl.hostname = clientUrl.hostname;
          locUrl.port = clientUrl.port;
          newHeaders.set("Location", locUrl.toString());
        }
      } catch (e) {
        if (location.startsWith("/")) {
          newHeaders.set("Location", location);
        }
      }
    }

    newHeaders.set("X-MILICONFIG-Gateway", "cloudflare");
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: newHeaders,
    });
  },
};
