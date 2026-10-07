import os
import asyncio
import logging
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from app.config import settings
from app.database import db
from app.services.repository import repo
from app.security.security import hash_password, verify_access_token
from app.api import auth, users, nodes, subscriptions, admin
from app.transports.websocket.handler import handle_websocket_connection
from app.transports.xhttp.handler import handle_xhttp_request
from app.protocols.shadowsocks.server import ss_server
from app.networking.dns import dns_resolver

_log_level = getattr(logging, str(settings.LOG_LEVEL or "INFO").strip().upper(), logging.INFO)
if not isinstance(_log_level, int):
    _log_level = logging.INFO
logging.basicConfig(level=_log_level)
logger = logging.getLogger("miliconfig.main")

app = FastAPI(
    title="MILICONFIG",
    description="Enterprise Multi-Protocol Proxy & Subscription Infrastructure",
    version="1.0.0"
)

# Absolute path resolution for templates and static to ensure zero directory resolution errors
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

def render_template(template_name: str, request: Request, context: Optional[Dict[str, Any]] = None) -> HTMLResponse:
    """
    Universal template renderer using direct Jinja2 rendering.
    Completely avoids Starlette TemplateResponse signature incompatibilities and 'unhashable type dict' errors.
    """
    ctx = context.copy() if context else {}
    ctx["request"] = request
    try:
        template = templates.get_template(template_name)
        return HTMLResponse(content=template.render(ctx))
    except Exception as e:
        logger.error(f"Error rendering template {template_name}: {e}", exc_info=True)
        file_path = os.path.join(TEMPLATES_DIR, template_name)
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as tf:
                return HTMLResponse(content=tf.read())
        return HTMLResponse(content=f"<h1>Template Rendering Error</h1><p>{e}</p>", status_code=500)

# Include API Routers
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(nodes.router)
app.include_router(subscriptions.router)
app.include_router(admin.router)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global unhandled exception on {request.method} {request.url.path}: {exc}", exc_info=True)
    return HTMLResponse(
        content=f"""
        <!DOCTYPE html>
        <html>
        <head><title>MILICONFIG - Server Recovery</title></head>
        <body style="background:#060907;color:#fff;font-family:sans-serif;padding:40px;text-align:center;">
          <h1 style="color:#00ff66;">MILICONFIG</h1>
          <p style="color:#9ca3af;">Server encountered an internal exception:</p>
          <pre style="background:#111;color:#ff5555;padding:20px;border-radius:8px;display:inline-block;text-align:left;max-width:800px;overflow:auto;">{str(exc)}</pre>
          <p style="margin-top:20px;"><a href="/login" style="color:#00ff66;margin-right:15px;">Go to Login</a><a href="/health" style="color:#00ff66;">System Health</a></p>
        </body>
        </html>
        """,
        status_code=500
    )

@app.on_event("startup")
async def on_startup():
    logger.info("Initializing MILICONFIG database schema...")
    try:
        db.init_schema()

        # Create default superadmin if not exists
        admin_user = repo.get_admin_by_username(settings.ADMIN_USERNAME)
        if not admin_user:
            pwd_hash = hash_password(settings.ADMIN_PASSWORD)
            repo.create_admin(settings.ADMIN_USERNAME, pwd_hash, role="superadmin")
            logger.info(f"Default superadmin '{settings.ADMIN_USERNAME}' created.")

        # Create default user if no users exist
        existing_users = repo.list_users()
        if not existing_users:
            default_user = repo.create_user(
                username="default",
                display_name="Default User",
                custom_uuid="351c9981-04b6-4103-aa4b-864aa9c91469"
            )
            logger.info(f"Initial default user created with UUID {default_user.uuid}")

        # Seed default nodes prioritizing direct Railway deployment (stanngv2 standard)
        existing_nodes = repo.list_nodes()
        existing_names = [n.name for n in existing_nodes]
        
        # 1. Railway Direct VLESS WS (Pure Railway without Cloudflare)
        if not any("ریل‌وی مستقیم" in name for name in existing_names):
            repo.create_node(
                name="miliconfig • 🚂 ریل‌وی مستقیم (Railway Direct)",
                protocol="vless",
                address="",  # Automatically resolves to current Railway domain e.g. milinewc2-production.up.railway.app
                port=443,
                network="ws",
                tls=True,
                path="/?ed=2048",
                region="Railway"
            )
        # 2. Railway Anti-DPI Fragment
        if not any("ضد فیلتر" in name for name in existing_names):
            repo.create_node(
                name="miliconfig • ⚡ ریل‌وی ضد فیلتر (Railway Fragment)",
                protocol="vless",
                address="",
                port=443,
                network="ws",
                tls=True,
                path="/ws",
                region="Railway-Fragment"
            )
        # 3. Railway xHTTP
        if not any("xHTTP" in name for name in existing_names):
            repo.create_node(
                name="miliconfig • 🚀 ریل‌وی xHTTP (Railway xHTTP)",
                protocol="vless",
                address="",
                port=443,
                network="xhttp",
                tls=True,
                path="/xhttp",
                region="Railway-xHTTP"
            )
        # 4. Railway Trojan Direct
        if not any("تروجان ریل‌وی" in name for name in existing_names):
            repo.create_node(
                name="miliconfig • 🔒 تروجان ریل‌وی (Railway Trojan)",
                protocol="trojan",
                address="",
                port=443,
                network="ws",
                tls=True,
                path="/?ed=2048",
                region="Railway-Trojan"
            )

        # Seed default proxy IPs if none exist
        existing_pips = repo.list_proxy_ips()
        if not existing_pips:
            default_pips = [
                ("104.16.132.229", 443, "CF"),
                ("104.17.147.22", 443, "CF"),
                ("141.101.90.10", 443, "CF"),
                ("162.158.228.87", 443, "CF")
            ]
            for ip, port, reg in default_pips:
                repo.add_proxy_ip(ip, port, reg)

        # Seed default DNS profile
        existing_dns = repo.list_dns_profiles()
        if not existing_dns:
            repo.create_dns_profile("AliDNS DoH", "https://223.5.5.5/dns-query", "doh", is_default=True)
            repo.create_dns_profile("Cloudflare DoH", "https://1.1.1.1/dns-query", "doh", is_default=False)

    except Exception as e:
        logger.error(f"Startup initialization error (non-fatal): {e}", exc_info=True)

    # Start ShadowSocks server
    if settings.ENABLE_SHADOWSOCKS:
        try:
            asyncio.create_task(ss_server.start())
        except Exception as e:
            logger.warning(f"ShadowSocks background listener initialization: {e}")

@app.on_event("shutdown")
async def on_shutdown():
    if settings.ENABLE_SHADOWSOCKS:
        await ss_server.stop()

# ---------------- HEALTH & READINESS ----------------
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "MILICONFIG",
        "version": "1.0.0"
    }

@app.get("/ready")
async def ready_check():
    try:
        conn = db.get_connection()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ready" if db_ok else "degraded",
        "database": "connected" if db_ok else "error",
        "shadowsocks_engine": "running" if ss_server.is_running else "stopped",
        "protocols": {
            "vless": settings.ENABLE_VLESS,
            "trojan": settings.ENABLE_TROJAN,
            "xhttp": settings.ENABLE_XHTTP,
            "shadowsocks": settings.ENABLE_SHADOWSOCKS
        }
    }

# ---------------- UI & WEBPAGE ROUTES ----------------
@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    token = request.cookies.get("miliconfig_token")
    if not token or not verify_access_token(token):
        return RedirectResponse(url="/login", status_code=302)
    return render_template("index.html", request)

@app.get("/login", response_class=HTMLResponse)
async def serve_login(request: Request):
    token = request.cookies.get("miliconfig_token")
    if token and verify_access_token(token):
        return RedirectResponse(url="/", status_code=302)
    return render_template("login.html", request)

# ---------------- xHTTP POST ROUTING ----------------
@app.post("/xhttp")
@app.post("/")
async def xhttp_endpoint(request: Request):
    if settings.ENABLE_XHTTP:
        return await handle_xhttp_request(request)
    return Response("xHTTP disabled", status_code=404)

# ---------------- WEBSOCKET ROUTING ----------------
@app.websocket("/ws")
@app.websocket("/")
@app.websocket("/{path_param:path}")
async def websocket_proxy_endpoint(websocket: WebSocket, path_param: str = ""):
    await handle_websocket_connection(websocket, path_param)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")
