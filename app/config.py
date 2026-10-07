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

    # ShadowSocks listener. RAILWAY_TCP_APPLICATION_PORT is injected by Railway when a
    # TCP proxy is enabled, so the engine binds to the port that is actually reachable.
    SS_PORT: int = _env_int("SS_PORT", _env_int("RAILWAY_TCP_APPLICATION_PORT", 8388))
    SS_BIND_HOST: str = os.environ.get("SS_BIND_HOST", "0.0.0.0")
    SS_DEFAULT_METHOD: str = os.environ.get("SS_DEFAULT_METHOD", "chacha20-ietf-poly1305")
    SS_DEFAULT_PASSWORD: str = os.environ.get("SS_DEFAULT_PASSWORD", "miliconfig_ss_pass_2026")

    # Public (client-facing) ShadowSocks endpoint. Railway only exposes the container
    # through the HTTP edge unless a TCP proxy is added; these variables let the
    # subscription engine advertise the real public host/port instead of a dead port.
    SS_PUBLIC_HOST: str = (
        os.environ.get("SS_PUBLIC_HOST") or os.environ.get("RAILWAY_TCP_PROXY_DOMAIN") or ""
    ).strip()
    SS_PUBLIC_PORT: int = _env_int("SS_PUBLIC_PORT", _env_int("RAILWAY_TCP_PROXY_PORT", 0))

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
    def ss_public_endpoint(self):
        """Explicit public ShadowSocks host/port, if configured."""
        host = self.SS_PUBLIC_HOST
        port = self.SS_PUBLIC_PORT or self.SS_PORT
        return (host, port) if host else (None, None)


settings = Settings()
