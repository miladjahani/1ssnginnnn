import os
from typing import List


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable without ever crashing the process."""
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


class Settings:
    PORT: int = _env_int("PORT", 8000)
    DATABASE_URL: str = os.environ.get("DATABASE_URL", "sqlite:///./miliconfig.db")
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "miliconfig-secret-key-super-secure-production-2026")
    ADMIN_USERNAME: str = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = os.environ.get("ADMIN_PASSWORD", "miliconfig_admin_2026")
    JWT_SECRET: str = os.environ.get("JWT_SECRET", "miliconfig-jwt-secret-key-32-chars-long-production")
    SUBSCRIPTION_SECRET: str = os.environ.get("SUBSCRIPTION_SECRET", "miliconfig-sub-secret-key-production")
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    PUBLIC_BASE_URL: str = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    DEFAULT_DOMAIN: str = os.environ.get("DEFAULT_DOMAIN", "localhost")
    DEFAULT_PATH: str = os.environ.get("DEFAULT_PATH", "/")
    DNS_SERVERS: str = os.environ.get("DNS_SERVERS", "1.1.1.1,8.8.8.8,https://223.5.5.5/dns-query")

    # Railway TCP proxy (raw TCP ingress). It targets exactly one container port; the
    # HTTP/WS ingress or the ShadowSocks listener - see the properties below.
    RAILWAY_TCP_APPLICATION_PORT: int = _env_int("RAILWAY_TCP_APPLICATION_PORT", 0)
    RAILWAY_TCP_PROXY_DOMAIN: str = (os.environ.get("RAILWAY_TCP_PROXY_DOMAIN") or "").strip()
    RAILWAY_TCP_PROXY_PORT: int = _env_int("RAILWAY_TCP_PROXY_PORT", 0)

    # Explicit overrides (also usable outside Railway).
    TCP_PROXY_HOST: str = (os.environ.get("TCP_PROXY_HOST") or "").strip()
    TCP_PROXY_PORT: int = _env_int("TCP_PROXY_PORT", 0)

    # ShadowSocks listener. It must never share the HTTP port, so the Railway TCP proxy
    # application port is only used when it is not the web port.
    SS_PORT: int = _env_int("SS_PORT", 0) or (
        RAILWAY_TCP_APPLICATION_PORT
        if RAILWAY_TCP_APPLICATION_PORT and RAILWAY_TCP_APPLICATION_PORT != PORT
        else 8388
    )
    SS_BIND_HOST: str = os.environ.get("SS_BIND_HOST", "0.0.0.0")
    SS_DEFAULT_METHOD: str = os.environ.get("SS_DEFAULT_METHOD", "chacha20-ietf-poly1305")
    SS_DEFAULT_PASSWORD: str = os.environ.get("SS_DEFAULT_PASSWORD", "miliconfig_ss_pass_2026")

    # Public (client-facing) ShadowSocks endpoint. Only advertised when it is actually
    # reachable, i.e. explicitly configured or served by the TCP proxy.
    SS_PUBLIC_HOST: str = (os.environ.get("SS_PUBLIC_HOST") or "").strip()
    SS_PUBLIC_PORT: int = _env_int("SS_PUBLIC_PORT", 0)

    # Protocol toggles
    ENABLE_VLESS: bool = os.environ.get("ENABLE_VLESS", "true").lower() in ("true", "1", "yes")
    ENABLE_TROJAN: bool = os.environ.get("ENABLE_TROJAN", "true").lower() in ("true", "1", "yes")
    ENABLE_XHTTP: bool = os.environ.get("ENABLE_XHTTP", "true").lower() in ("true", "1", "yes")
    ENABLE_SHADOWSOCKS: bool = os.environ.get("ENABLE_SHADOWSOCKS", "true").lower() in ("true", "1", "yes")

    # Outbound mode: "" (proxy first with fallback), "no" (direct first with proxy fallback), "only" (proxy only)
    OUTBOUND_MODE: str = os.environ.get("OUTBOUND_MODE", "")
    OUTBOUND_PROXY: str = os.environ.get("OUTBOUND_PROXY", "")

    @property
    def dns_server_list(self) -> List[str]:
        return [s.strip() for s in self.DNS_SERVERS.split(",") if s.strip()]

    @property
    def running_on_railway(self) -> bool:
        """True when the process is hosted by Railway (injects RAILWAY_* variables)."""
        return any(k.startswith("RAILWAY_") for k in os.environ)

    @property
    def tcp_proxy_targets_http(self) -> bool:
        """True when the TCP proxy forwards raw TCP to the HTTP/WebSocket port."""
        if self.TCP_PROXY_HOST:
            return True
        if self.RAILWAY_TCP_APPLICATION_PORT:
            return self.RAILWAY_TCP_APPLICATION_PORT == self.PORT
        # Railway did not report the target port: assume the web port, which is what
        # keeps the panel and proxy WebSockets reachable without a TLS domain.
        return bool(self.RAILWAY_TCP_PROXY_DOMAIN)

    @property
    def tcp_proxy_targets_ss(self) -> bool:
        """True when the TCP proxy forwards raw TCP to the ShadowSocks listener."""
        return bool(self.RAILWAY_TCP_APPLICATION_PORT) and (
            self.RAILWAY_TCP_APPLICATION_PORT == self.SS_PORT
        )

    @property
    def http_tcp_proxy_endpoint(self):
        """
        Public plain-TCP endpoint carrying the panel and the proxy WebSocket paths.

        Needed when the `*.up.railway.app` HTTPS hostname is filtered by SNI: plain
        WebSocket has no SNI to block.
        """
        host = self.TCP_PROXY_HOST
        port = self.TCP_PROXY_PORT
        if not (host and port) and self.tcp_proxy_targets_http:
            host = host or self.RAILWAY_TCP_PROXY_DOMAIN
            port = port or self.RAILWAY_TCP_PROXY_PORT
        return (host, port) if host and port else (None, None)

    @property
    def ss_public_endpoint(self):
        """Public ShadowSocks host/port, only when it is genuinely reachable."""
        if self.SS_PUBLIC_HOST and self.SS_PUBLIC_PORT:
            return (self.SS_PUBLIC_HOST, self.SS_PUBLIC_PORT)
        if self.tcp_proxy_targets_ss and self.RAILWAY_TCP_PROXY_DOMAIN and self.RAILWAY_TCP_PROXY_PORT:
            return (self.RAILWAY_TCP_PROXY_DOMAIN, self.RAILWAY_TCP_PROXY_PORT)
        if self.SS_PUBLIC_HOST:
            return (self.SS_PUBLIC_HOST, self.SS_PORT)
        return (None, None)


settings = Settings()
