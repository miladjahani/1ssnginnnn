/**
 * MILICONFIG - Cloudflare Pages & Workers Unblocked Edge Gateway
 * 
 * این اسکریپت تمام ترافیک پنل ادمین، سابسکریپشن‌ها و کانکشن‌های وب‌سوکت را
 * از بستر شبکه جهانی کلودفلر (بدون فیلتر در ایران) به بک‌اند ریل‌وی هدایت می‌کند.
 */

const BACKEND_DOMAIN = "milinewc2-production.up.railway.app";

export default {
  async fetch(request, env, ctx) {
    const targetHost = env.BACKEND_HOST || BACKEND_DOMAIN;
    const clientUrl = new URL(request.url);
    
    // Build target URL on Railway
    const targetUrl = new URL(request.url);
    targetUrl.hostname = targetHost;
    targetUrl.protocol = "https:";
    targetUrl.port = "443";

    const clientHost = request.headers.get("Host") || targetHost;

    const headers = new Headers(request.headers);
    headers.set("Host", targetHost);
    headers.set("X-Forwarded-Host", clientHost);
    headers.set("X-Forwarded-Proto", "https");

    // Handle WebSocket Proxying (VLESS & Trojan WS)
    const upgrade = request.headers.get("Upgrade");
    if (upgrade && upgrade.toLowerCase() === "websocket") {
      return fetch(targetUrl.toString(), {
        method: request.method,
        headers: headers,
        body: request.body,
        redirect: "manual"
      });
    }

    // Standard HTTP/HTTPS Proxying
    const response = await fetch(new Request(targetUrl.toString(), {
      method: request.method,
      headers: headers,
      body: request.body,
      redirect: "manual"
    }));

    // If backend returns a redirect (e.g. / -> /login or /login -> /),
    // rewrite the Location header so the user stays on the unblocked domain!
    const location = response.headers.get("Location");
    if (location && (response.status === 301 || response.status === 302 || response.status === 307 || response.status === 308)) {
      const newHeaders = new Headers(response.headers);
      try {
        const locUrl = new URL(location, request.url);
        if (locUrl.hostname === targetHost) {
          locUrl.hostname = clientUrl.hostname;
          locUrl.protocol = clientUrl.protocol;
          locUrl.port = clientUrl.port;
          newHeaders.set("Location", locUrl.toString());
        }
      } catch (e) {
        // Fallback relative redirect
        if (location.startsWith("/")) {
          newHeaders.set("Location", location);
        }
      }
      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: newHeaders
      });
    }

    return response;
  }
};
