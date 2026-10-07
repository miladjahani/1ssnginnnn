"""Startup seeding helpers for platform-specific client endpoints.

Railway's HTTP edge terminates TLS on `*.up.railway.app`, a hostname that Iranian
ISPs filter by SNI. Railway can additionally expose a raw *TCP proxy*
(`RAILWAY_TCP_PROXY_DOMAIN` / `RAILWAY_TCP_PROXY_PORT`) that forwards straight to the
container port, which carries both the panel and the VLESS/Trojan WebSocket endpoints.
Plain WebSocket (no TLS, no SNI) therefore stays reachable when the HTTPS hostname is
filtered, so the matching client nodes are seeded automatically.
"""
import logging
from typing import Optional, List

from app.config import settings
from app.services.repository import repo

logger = logging.getLogger("miliconfig.bootstrap")

TCP_PROXY_NODE_NAMES = {
    "vless": "miliconfig • 🔓 ریل‌وی بدون TLS (Railway TCP Proxy)",
    "trojan": "miliconfig • 🔓 تروجان بدون TLS (Railway TCP Proxy)",
}


def tcp_proxy_endpoint() -> Optional[tuple]:
    """Public (host, port) of the raw-TCP ingress carrying the web/WS port."""
    host, port = settings.http_tcp_proxy_endpoint
    if host and port:
        return str(host), int(port)
    return None


def seed_railway_tcp_proxy_nodes(repository=None, endpoint: Optional[tuple] = None) -> List[object]:
    """
    Create the no-TLS WebSocket nodes for the Railway TCP proxy endpoint.

    Idempotent: an existing disabled/enabled node with the same address+port is reused
    instead of duplicated on every restart.
    """
    repo_ = repository or repo
    target = endpoint or tcp_proxy_endpoint()
    if not target:
        return []

    host, port = target
    created = []

    existing = repo_.list_nodes()
    already = [n for n in existing if (n.address or "").strip() == host and int(n.port or 0) == int(port)]
    if already:
        return []

    for protocol in ("vless", "trojan"):
        node = repo_.create_node(
            name=TCP_PROXY_NODE_NAMES[protocol],
            protocol=protocol,
            address=host,
            port=port,
            network="ws",
            tls=False,
            path="/?ed=2048",
            region="Railway-TCP",
        )
        created.append(node)
        logger.info(
            "Seeded no-TLS %s node for the Railway TCP proxy at %s:%s", protocol.upper(), host, port
        )
    return created
