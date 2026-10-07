# MILICONFIG

[![Python 3.12](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg)](https://fastapi.tiangolo.com)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
[![Railway](https://img.shields.io/badge/Railway-Deploy-0B0D0E.svg)](https://railway.app/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**MILICONFIG** is a high-performance, asynchronous Python-based proxy server, management dashboard, and multi-format subscription generator. It is the sovereign, cloud-agnostic rewrite of the Cloudflare-bound [`byjoey/cfnew`](https://github.com/byjoey/cfnew) project, redesigned from the ground up for containerized deployment on **Railway**, Docker, and modern Linux environments.

[🇮🇷 فارسی (Persian Documentation)](./README.fa.md)

---

## Key Highlights

- **Zero Cloudflare Lock-in**: Fully implemented in pure Python (FastAPI + Asyncio) without dependencies on Cloudflare Workers, Pages, or proprietary `cloudflare:sockets` runtime APIs.
- **Enterprise Multi-User Core**: Real relational database backing with user accounts, custom UUIDs, expiry dates, upload/download bandwidth quotas, and session tracking.
- **Real Protocol Implementations**:
  - **VLESS**: Binary packet parser with UUID auth, TCP stream, and UDP/DNS proxying.
  - **Trojan**: SHA224 password hashing, CRLF frame validation, and streaming.
  - **xHTTP**: HTTP POST chunked streaming transport with anti-DPI randomized padding headers.
  - **ShadowSocks (AEAD)**: Native Python asyncio TCP server supporting `chacha20-ietf-poly1305`, `aes-256-gcm`, and `aes-128-gcm` with per-user credentials (UDP credentials are stored per user but the UDP relay is not implemented yet).
- **Client-Facing Node Naming**: All client-visible nodes are strictly prefixed with `miliconfig` (e.g. `miliconfig-01 • US Premium`, `miliconfig • VLESS`, `miliconfig • ShadowSocks`).
- **Universal Subscription Engine**:
  - Automatically identifies client `User-Agent` (Clash, Sing-box, V2RayNG, Shadowrocket, Surge, Loon, etc.).
  - Emits native Clash/Mihomo YAML, Sing-box JSON (v1.12+), and Base64 link lists.
  - Explicit target overrides via `?target=clash`, `?target=singbox`, `?target=v2ray`.
- **Dark Glassmorphic UI**: High-end Neon Green & Dark Carbon SaaS control panel with zero simulated or fake metrics.
- **Production-Ready & Railway-Ready**: Runs straight from GitHub on Railway using standard Dockerfile with dynamic `$PORT` binding.

---

## Architecture Overview

```
                           +----------------------------------------+
                           |           Internet Clients             |
                           +----------------------------------------+
                                        |              |
                    HTTPS / WSS / xHTTP |              | ShadowSocks TCP
                                        v              v
+-----------------------------------------------------------------------------------+
| Railway / Docker Container Environment                                            |
|                                                                                   |
|  +-----------------------------------------------------------------------------+  |
|  |                            Uvicorn Web Server                               |  |
|  |  +-----------------------------------------------------------------------+  |  |
|  |  |                      FastAPI Core Application                         |  |  |
|  |  |                                                                       |  |  |
|  |  |  [ Web UI & Admin Panel ]   [ Subscription Engine ]   [ REST API ]    |  |  |
|  |  |  - Glassmorphic Neon UI     - Auto User-Agent Detect  - Auth (JWT)    |  |  |
|  |  |  - Multi-User Management    - Clash / Sing-box / V2   - Users & Nodes |  |  |
|  |  |  - Node & ProxyIP Control   - miliconfig-prefixed     - Settings/Logs |  |  |
|  |  +-----------------------------------------------------------------------+  |  |
|  |                                                                             |  |
|  |  +-----------------------------------------------------------------------+  |  |
|  |  |                      Transport & Protocol Layer                       |  |  |
|  |  |  - WebSocket Endpoint (VLESS / Trojan multiplexer)                    |  |  |
|  |  |  - xHTTP Streaming Endpoint (POST chunked + padding)                  |  |  |
|  |  +-----------------------------------------------------------------------+  |  |
|  +-----------------------------------------------------------------------------+  |
|                                                                                   |
|  +-----------------------------------------------------------------------------+  |
|  |                      ShadowSocks Asyncio Engine                             |  |
|  |  - Native Python TCP Relay Server (AEAD ShadowSocks)                        |  |
|  |  - Standard AEAD Ciphers (chacha20-poly1305, aes-256-gcm, aes-128-gcm)      |  |
|  |  - Per-User Credentials & Dynamic Multi-Port Listener                       |  |
|  +-----------------------------------------------------------------------------+  |
|                                       |                                           |
|                                       v                                           |
|  +-----------------------------------------------------------------------------+  |
|  |                      Outbound Networking & Routing Engine                   |  |
|  |  - Direct TCP Socket Relay                                                  |  |
|  |  - SOCKS5 Outbound Client (with Auth)                                       |  |
|  |  - HTTP/HTTPS CONNECT Tunnel Client                                         |  |
|  |  - ProxyIP Pool Manager & Failover Rotator                                  |  |
|  |  - Async DNS Resolver (DoH, UDP, TCP)                                       |  |
|  |  - Rule-Based Routing (Domain, CIDR, Port, Mode qj)                         |  |
|  +-----------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

---

## Quick Start

### 1. Local Development
```bash
# Clone the repository
git clone https://github.com/your-username/miliconfig.git
cd miliconfig

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start development server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Open [http://localhost:8000](http://localhost:8000) in your browser.
Default credentials:
- **Username**: `admin`
- **Password**: `miliconfig_admin_2026`

---

### 2. Docker & Docker Compose
To run MILICONFIG along with a dedicated PostgreSQL database:
```bash
# Start the full stack
docker compose up -d

# Check logs
docker compose logs -f miliconfig
```
The application will automatically initialize the database schema and be accessible at port `8000`.

---

### 3. Railway Deployment

MILICONFIG is fully pre-configured for Railway:
1. Log in to [Railway.app](https://railway.app/).
2. Create a **New Project** and select **Deploy from GitHub repo**.
3. Choose your `miliconfig` repository (Railway detects `Dockerfile` via `railway.json` / `railway.toml`).
4. Add a **Volume** to the service and mount it at `/data`, so the SQLite database survives redeploys.
5. In your MILICONFIG service settings set `SECRET_KEY`, `ADMIN_PASSWORD`, `JWT_SECRET` and (optionally) `PUBLIC_BASE_URL` / `DEFAULT_DOMAIN` to your Railway domain.
6. Click **Deploy**. Railway assigns a public HTTPS domain and routes traffic (panel, subscriptions and VLESS/Trojan WebSockets) to the container.

> **Database note:** this build ships its own SQLite engine. `DATABASE_URL` accepts `sqlite:///...`
> paths; a PostgreSQL URL cannot be honoured and the service logs a warning and falls back to
> `/data/miliconfig.db`. Attach a Railway **Volume at `/data`** for persistence.

#### Optional: ShadowSocks on Railway
Railway only publishes the container through its HTTPS edge, so the ShadowSocks listener
(`SS_PORT`, default `8388`) is not reachable from the internet by default:
1. Enable **TCP Proxy** for the service in Railway and target the same port you set in `SS_PORT`
   (Railway then injects `RAILWAY_TCP_APPLICATION_PORT`, `RAILWAY_TCP_PROXY_DOMAIN` and
   `RAILWAY_TCP_PROXY_PORT`).
2. The subscription engine automatically advertises the public `RAILWAY_TCP_PROXY_DOMAIN:RAILWAY_TCP_PROXY_PORT`
   endpoint. Without a TCP proxy the `ss://` node is intentionally left out of subscriptions so
   clients never receive a dead configuration.

---

## Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `PORT` | Web and proxy listening port | `8000` |
| `DATABASE_URL` | SQLite connection string (`sqlite:///...`) | `sqlite:///./miliconfig.db` |
| `DATA_DIR` | Directory holding the SQLite file (mount a Railway Volume here) | `/data` if mounted, else app dir |
| `SECRET_KEY` | Application encryption secret | Random hex string |
| `JWT_SECRET` | Admin session JWT signature key | Random hex string |
| `ADMIN_USERNAME` | Superadmin username | `admin` |
| `ADMIN_PASSWORD` | Superadmin password | `miliconfig_admin_2026` |
| `PUBLIC_BASE_URL` | Public HTTPS URL for subscription generation | `http://localhost:8000` |
| `DEFAULT_DOMAIN` | Fallback SNI/Host domain | `localhost` |
| `ENABLE_SHADOWSOCKS` | Enable background ShadowSocks server | `true` |
| `SS_PORT` | ShadowSocks TCP listening port (falls back to `RAILWAY_TCP_APPLICATION_PORT`) | `8388` |
| `SS_PUBLIC_HOST` / `SS_PUBLIC_PORT` | Public ShadowSocks endpoint advertised in subscriptions (falls back to `RAILWAY_TCP_PROXY_DOMAIN` / `RAILWAY_TCP_PROXY_PORT`) | unset |
| `SS_DEFAULT_METHOD`| Default AEAD cipher | `chacha20-ietf-poly1305` |
| `OUTBOUND_MODE` | Outbound mode (`""`, `no`, `only`) | `""` |
| `OUTBOUND_PROXY` | Upstream proxy (`socks5://...` or `http://...`)| `""` |
| `DNS_SERVERS` | Upstream DNS / DoH endpoints | `1.1.1.1,8.8.8.8,https://223.5.5.5/dns-query`|

---

## Direct Railway Deployment (StanNG Architecture & Anti-DPI)

MILICONFIG is fully optimized for **direct Railway deployment** with zero Cloudflare requirement, following the architecture of projects like `stanngv2`:

### 1. One-Click Railway Deployment
1. Push or fork this repository to your GitHub account.
2. In [Railway.app](https://railway.app) -> **New Project** -> **Deploy from GitHub repo**.
3. Railway automatically detects `railway.json` / `Dockerfile` and executes `python3 main.py`.
4. Railway assigns your public HTTPS domain (e.g., `https://milinewc2-production.up.railway.app`).

### 2. Built-in Direct Nodes & Anti-DPI Fragment
Because Railway uses its own dedicated cloud IP pool, Iranian ISPs attempt to filter the domain SNI (`*.up.railway.app`). MILICONFIG solves this natively:
- **Direct VLESS WebSocket (`miliconfig • 🚂 ریل‌وی مستقیم`)**: Connects directly to Railway port 443 with TLS.
- **Anti-DPI Fragment (`miliconfig • ⚡ ریل‌وی ضد فیلتر`)**: Automatically configured in Sing-box subscriptions (`fragment: {size: '10-30', sleep: '10-20'}`) to split the TLS ClientHello packet.
- **For v2rayNG / Xray Clients**:
  In v2rayNG -> **Settings** -> **Advanced settings** -> **Fragment**:
  - Packets: `1-3`
  - Length: `10-20`
  - Interval: `10-20`
  Once enabled, all configs achieve immediate low latency and real ping directly to Railway without needing Cloudflare!

### 3. Web Panel Access & Authentication
- Default Superadmin: `admin`
- Default Password: `miliconfig_admin_2026`
- Management Dashboard: `https://<your-railway-domain>/`
- Subscription Link: `https://<your-railway-domain>/sub/<subscription_token>`

---

## Migration from `byjoey/cfnew`

If you are migrating from an existing Cloudflare Worker / Pages deployment of `cfnew`, use the automated migration script:

```bash
# Migrate from an exported Cloudflare KV JSON file
python -m app.migrate_cfnew --file cfnew_kv_export.json

# Or migrate directly from environment variables:
export u="your-uuid"
export p="104.16.0.1:443"
export s="socks5://user:pass@host:1080"
export yx="1.1.1.1:443#Singapore,8.8.8.8:443#Google"
python -m app.migrate_cfnew --from-env
```

The migration utility will:
1. Create a dedicated user for the cfnew UUID.
2. Import upstream proxy and routing settings.
3. Import ProxyIPs and preferred IP lists as nodes prefixed with `miliconfig • `.
4. Configure DoH DNS endpoints.

---

## Testing

Run the test suite:
```bash
python scripts/run_tests.py
```
Test suite coverage:
- `test_auth.py`: Password hashing, JWT creation/verification, rate limiting.
- `test_users.py`: User lifecycle, quotas, UUID/token resets, traffic recording.
- `test_vless.py`: VLESS v0 binary parsing, commands, address types.
- `test_trojan.py`: Trojan SHA224 hash computation and frame parsing.
- `test_shadowsocks.py`: AEAD crypto (ChaCha20-Poly1305, AES-GCM), live server integration.
- `test_subscription.py`: Base64, Clash, Sing-box formats, User-Agent detection, `miliconfig-` naming enforcement.
- `test_dns.py`: Async DoH resolution, system resolver, cache.
- `test_routing.py`: Rule-based outbound routing (domain, CIDR, port).
- `test_proxyip.py`: ProxyIP pool management and health tracking.
- `test_migration.py`: Automated cfnew KV configuration migration.

---

## License

This project is licensed under the MIT License.
