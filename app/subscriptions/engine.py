import json
import os
import base64
import yaml
import urllib.parse
from typing import List, Dict, Any, Optional, Tuple
from app.config import settings
from app.models.models import User, Node, ShadowSocksCredential
from app.services.repository import repo
from app.services.access import user_access_error

class SubscriptionEngine:
    def format_client_node_name(self, base_name: str, index: int = 1, region: str = "US", protocol: str = "VLESS") -> str:
        """
        Enforce mandatory MILICONFIG client-facing node naming convention:
        Must start with 'miliconfig'.
        """
        raw = base_name.strip()
        if raw.lower().startswith("miliconfig"):
            return raw
        return f"miliconfig-{index:02d} • {region} ({protocol})"

    # ---------------- UNBLOCKED PUBLIC ROUTING ----------------
    @staticmethod
    def hostname_of(value: Optional[str]) -> str:
        """Extract a bare hostname from a URL, domain or host:port value."""
        raw = (value or "").strip()
        if not raw:
            return ""
        if "//" not in raw:
            raw = "//" + raw
        try:
            parsed = urllib.parse.urlsplit(raw)
            return (parsed.hostname or "").strip()
        except Exception:
            return ""

    def resolve_public_host(self, request_host: Optional[str] = None) -> str:
        """
        Decide which domain the generated client configurations must point at.

        Iranian ISPs block `*.up.railway.app` (TLS SNI filtering), so an explicitly
        configured public domain always wins over the host of the incoming request.
        Priority: panel setting -> PUBLIC_BASE_URL env -> request host -> DEFAULT_DOMAIN.
        """
        configured = self.hostname_of(repo.get_setting("public_base_url", ""))
        if configured:
            return configured
        env_host = self.hostname_of(os.environ.get("PUBLIC_BASE_URL", ""))
        if env_host and env_host.lower() not in ("localhost", "127.0.0.1"):
            return env_host
        req = (request_host or "").split(":")[0].strip()
        return req or settings.DEFAULT_DOMAIN

    def gateway_domain(self) -> str:
        """
        Front domain (custom domain or Cloudflare gateway) used as SNI/Host for the
        clean-IP mirror nodes. Explicit opt-in: without it only the public domain
        nodes are generated.
        """
        configured = self.hostname_of(repo.get_setting("gateway_domain", ""))
        if configured:
            return configured
        return self.hostname_of(os.environ.get("GATEWAY_DOMAIN", ""))

    def clean_ip_endpoints(self, limit: int = 2) -> List[str]:
        """
        Preferred ("clean") edge IPs used when the front domain's own IPs are filtered.

        Admin-managed ProxyIP pool entries are reused (they already carry health and
        latency data), with an explicit `clean_ips` setting taking priority.
        """
        configured = repo.get_setting("clean_ips", "") or os.environ.get("GATEWAY_CLEAN_IPS", "")
        raw_ips = [ip.strip() for ip in (configured or "").split(",") if ip.strip()]
        if not raw_ips:
            raw_ips = [p.address.strip() for p in repo.list_proxy_ips(active_only=True) if p.address]
        seen: List[str] = []
        for ip in raw_ips:
            if ip and ip not in seen:
                seen.append(ip)
        return seen[:limit]

    def build_gateway_nodes(self, nodes: List[Node]) -> List[Node]:
        """
        Build in-memory mirror nodes that reach the service through a clean IP while
        keeping the gateway domain as TLS SNI/Host (the standard anti-filter pattern).

        Returns an empty list unless a gateway domain and at least one clean IP exist.
        """
        domain = self.gateway_domain()
        if not domain:
            return []
        ips = self.clean_ip_endpoints()
        if not ips:
            return []

        templates: Dict[str, Node] = {}
        for node in nodes:
            proto = (node.protocol or "vless").lower()
            if proto not in templates:
                templates[proto] = node
        if not templates:
            return []

        variants: List[Node] = []
        for ip in ips:
            for proto, node in templates.items():
                variants.append(Node(
                    id=None,
                    name=f"{node.name} • IP {ip}",
                    protocol=node.protocol,
                    address=ip,
                    port=443,
                    uuid=node.uuid,
                    password=node.password,
                    path=node.path,
                    host=domain,
                    sni=domain,
                    alpn=node.alpn,
                    network=node.network,
                    tls=True,
                    proxyip=node.proxyip,
                    region=node.region,
                    enabled=True,
                ))
        return variants

    def resolve_effective_address_and_port(self, node: Node, request_host: Optional[str] = None) -> Tuple[str, int, bool]:
        """
        Dynamically determine external server address, port, and TLS.
        If node address is local or empty, replace with the public request host.
        """
        addr = (node.address or "").strip()
        port = node.port
        tls = bool(node.tls)

        clean_host = (request_host or "").split(":")[0].strip()
        if not clean_host:
            clean_host = settings.DEFAULT_DOMAIN

        if not addr or addr in ("127.0.0.1", "localhost", "0.0.0.0"):
            addr = clean_host
            port = 443
            tls = True

        if "railway.app" in addr or "up.railway.app" in addr:
            port = 443
            tls = True

        return addr, port, tls

    def resolve_domain(self, node: Node, request_host: Optional[str] = None) -> str:
        clean_host = (request_host or "").split(":")[0].strip() or settings.DEFAULT_DOMAIN
        return node.sni or node.host or clean_host

    def resolve_ss_endpoint(self, ss_cred: ShadowSocksCredential, request_host: Optional[str] = None) -> Tuple[str, int]:
        """
        Resolve the client-facing ShadowSocks host/port.

        Railway only exposes the container through the HTTPS edge unless a TCP
        proxy is configured, so an explicit public endpoint (SS_PUBLIC_HOST /
        SS_PUBLIC_PORT or the RAILWAY_TCP_PROXY_* variables) always wins.
        """
        host = (ss_cred.server or "").strip()
        port = int(ss_cred.port or settings.SS_PORT)

        public_host, public_port = settings.ss_public_endpoint
        if public_host:
            return public_host, int(public_port or port)

        if not host or host in ("127.0.0.1", "localhost", "0.0.0.0"):
            host = (request_host or "").split(":")[0].strip() or settings.DEFAULT_DOMAIN
        return host, port

    def is_ss_publishable(self, ss_cred: Optional[ShadowSocksCredential]) -> bool:
        """Never advertise a ShadowSocks node that clients cannot actually reach."""
        if not ss_cred or not ss_cred.enabled:
            return False
        public_host, _ = settings.ss_public_endpoint
        if public_host:
            return True
        host = (ss_cred.server or "").strip()
        if host and host not in ("127.0.0.1", "localhost", "0.0.0.0"):
            return True
        # Local listener: reachable in local/VPS deployments, but on Railway the
        # port is not exposed to the internet without a TCP proxy.
        return not settings.running_on_railway

    def generate_vless_uri(self, user: User, node: Node, index: int = 1, request_host: Optional[str] = None) -> str:
        addr, port, tls = self.resolve_effective_address_and_port(node, request_host)
        domain = self.resolve_domain(node, request_host)
        name = self.format_client_node_name(node.name, index, node.region, "VLESS")
        security = "tls" if tls else "none"
        alpn_param = f"&alpn={node.alpn}" if node.alpn else "&alpn=h2,http/1.1"
        path = node.path or "/?ed=2048"
        if not path.startswith("/"):
            path = "/" + path
        path_encoded = urllib.parse.quote(path)

        query_params = (
            f"type={node.network}&security={security}&encryption=none"
            f"&host={domain}&sni={domain}&path={path_encoded}{alpn_param}&fp=chrome"
        )
        tag = urllib.parse.quote(name)
        return f"vless://{user.uuid}@{addr}:{port}?{query_params}#{tag}"

    def generate_trojan_uri(self, user: User, node: Node, index: int = 1, request_host: Optional[str] = None) -> str:
        addr, port, tls = self.resolve_effective_address_and_port(node, request_host)
        domain = self.resolve_domain(node, request_host)
        name = self.format_client_node_name(node.name, index, node.region, "Trojan")
        security = "tls" if tls else "none"
        alpn_param = f"&alpn={node.alpn}" if node.alpn else "&alpn=h2,http/1.1"
        path = node.path or "/?ed=2048"
        if not path.startswith("/"):
            path = "/" + path
        path_encoded = urllib.parse.quote(path)

        query_params = (
            f"type={node.network}&security={security}"
            f"&host={domain}&sni={domain}&path={path_encoded}{alpn_param}&fp=chrome"
        )
        tag = urllib.parse.quote(name)
        password = node.password or user.uuid
        return f"trojan://{password}@{addr}:{port}?{query_params}#{tag}"

    def generate_shadowsocks_uri(self, user: User, ss_cred: ShadowSocksCredential, index: int = 1, request_host: Optional[str] = None) -> str:
        name = f"miliconfig-{index:02d} • SS ({ss_cred.method})"
        user_info = f"{ss_cred.method}:{ss_cred.password}"
        b64_info = base64.urlsafe_b64encode(user_info.encode("utf-8")).decode("utf-8").rstrip("=")
        host, port = self.resolve_ss_endpoint(ss_cred, request_host)
        tag = urllib.parse.quote(name)
        return f"ss://{b64_info}@{host}:{port}#{tag}"

    def generate_base64_subscription(self, user: User, nodes: List[Node], ss_cred: Optional[ShadowSocksCredential] = None, request_host: Optional[str] = None) -> str:
        """Generate standard Base64 URI list for V2Ray, V2RayNG, Shadowrocket, Nekoray."""
        links = []
        for idx, node in enumerate(nodes, start=1):
            if node.protocol.lower() == "vless":
                links.append(self.generate_vless_uri(user, node, idx, request_host))
            elif node.protocol.lower() == "trojan":
                links.append(self.generate_trojan_uri(user, node, idx, request_host))

        if self.is_ss_publishable(ss_cred):
            links.append(self.generate_shadowsocks_uri(user, ss_cred, len(links) + 1, request_host))

        raw_text = "\n".join(links)
        return base64.b64encode(raw_text.encode("utf-8")).decode("utf-8")

    def generate_clash_yaml(self, user: User, nodes: List[Node], ss_cred: Optional[ShadowSocksCredential] = None, request_host: Optional[str] = None) -> str:
        """Generate Clash / Mihomo YAML subscription with automatic latency testing group."""
        proxies = []
        proxy_names = []

        for idx, node in enumerate(nodes, start=1):
            addr, port, tls = self.resolve_effective_address_and_port(node, request_host)
            domain = self.resolve_domain(node, request_host)
            client_name = self.format_client_node_name(node.name, idx, node.region, node.protocol.upper())
            proxy_names.append(client_name)
            
            p_dict = {
                "name": client_name,
                "type": node.protocol.lower(),
                "server": addr,
                "port": port,
                "uuid": user.uuid if node.protocol.lower() == "vless" else None,
                "password": (node.password or user.uuid) if node.protocol.lower() == "trojan" else None,
                "network": node.network,
                "tls": tls,
                "udp": True,
                "client-fingerprint": "chrome"
            }
            if tls:
                p_dict["servername"] = domain
                p_dict["skip-cert-verify"] = False
                alpn_val = node.alpn or "h2,http/1.1"
                p_dict["alpn"] = [a.strip() for a in alpn_val.split(",") if a.strip()]

            ws_path = node.path or "/?ed=2048"
            if not ws_path.startswith("/"):
                ws_path = "/" + ws_path

            if node.network == "ws":
                p_dict["ws-opts"] = {
                    "path": ws_path,
                    "headers": {"Host": domain}
                }
            elif node.network == "xhttp":
                p_dict["xhttp-opts"] = {
                    "path": ws_path,
                    "headers": {"Host": domain}
                }
            
            proxies.append({k: v for k, v in p_dict.items() if v is not None})

        if self.is_ss_publishable(ss_cred):
            ss_name = f"miliconfig-{len(proxy_names)+1:02d} • SS ({ss_cred.method})"
            ss_host, ss_port = self.resolve_ss_endpoint(ss_cred, request_host)
            proxy_names.append(ss_name)
            proxies.append({
                "name": ss_name,
                "type": "ss",
                "server": ss_host,
                "port": ss_port,
                "cipher": ss_cred.method,
                "password": ss_cred.password,
                "udp": bool(ss_cred.udp)
            })

        auto_group = "⚡ miliconfig • اتوماتیک (سریع‌ترین)"
        select_group = "🚀 miliconfig • انتخاب دستی"
        
        proxy_groups = [
            {
                "name": select_group,
                "type": "select",
                "proxies": [auto_group] + proxy_names + ["DIRECT"]
            },
            {
                "name": auto_group,
                "type": "url-test",
                "url": "https://www.gstatic.com/generate_204",
                "interval": 180,
                "tolerance": 50,
                "proxies": proxy_names
            }
        ]

        clash_config = {
            "port": 7890,
            "socks-port": 7891,
            "allow-lan": False,
            "mode": "rule",
            "log-level": "info",
            "ipv6": False,
            "dns": {
                "enable": True,
                "enhanced-mode": "fake-ip",
                "nameserver": ["1.1.1.1", "8.8.8.8", "https://223.5.5.5/dns-query"]
            },
            "proxies": proxies,
            "proxy-groups": proxy_groups,
            "rules": [
                "GEOIP,LAN,DIRECT,no-resolve",
                "GEOIP,CN,DIRECT,no-resolve",
                f"MATCH,{select_group}"
            ]
        }
        return yaml.dump(clash_config, sort_keys=False, allow_unicode=True)

    def generate_singbox_json(self, user: User, nodes: List[Node], ss_cred: Optional[ShadowSocksCredential] = None, request_host: Optional[str] = None) -> str:
        """Generate Sing-box JSON subscription compatible with v1.12+."""
        outbounds = []
        tags = []

        for idx, node in enumerate(nodes, start=1):
            addr, port, tls = self.resolve_effective_address_and_port(node, request_host)
            domain = self.resolve_domain(node, request_host)
            tag = self.format_client_node_name(node.name, idx, node.region, node.protocol.upper())
            tags.append(tag)

            ob = {
                "type": node.protocol.lower(),
                "tag": tag,
                "server": addr,
                "server_port": port,
            }
            if node.protocol.lower() == "vless":
                ob["uuid"] = user.uuid
            elif node.protocol.lower() == "trojan":
                ob["password"] = node.password or user.uuid

            if tls:
                ob["tls"] = {
                    "enabled": True,
                    "server_name": domain,
                    "insecure": False
                }
                alpn_val = node.alpn or "h2,http/1.1"
                ob["tls"]["alpn"] = [a.strip() for a in alpn_val.split(",") if a.strip()]
                # Fragment Anti-DPI for direct Railway connection (matches stanngv2 technique)
                if "railway.app" in domain or "railway.app" in addr or "Fragment" in node.name or "ضد فیلتر" in node.name:
                    ob["tls"]["fragment"] = {
                        "enabled": True,
                        "size": "10-30",
                        "sleep": "10-20"
                    }

            ws_path = node.path or "/?ed=2048"
            if not ws_path.startswith("/"):
                ws_path = "/" + ws_path

            if node.network == "ws":
                ob["transport"] = {
                    "type": "ws",
                    "path": ws_path,
                    "headers": {"Host": domain}
                }
            outbounds.append(ob)

        if self.is_ss_publishable(ss_cred):
            ss_tag = f"miliconfig-{len(tags)+1:02d} • SS ({ss_cred.method})"
            ss_host, ss_port = self.resolve_ss_endpoint(ss_cred, request_host)
            tags.append(ss_tag)
            outbounds.append({
                "type": "shadowsocks",
                "tag": ss_tag,
                "server": ss_host,
                "server_port": ss_port,
                "method": ss_cred.method,
                "password": ss_cred.password
            })

        selector_group = {
            "type": "selector",
            "tag": "🚀 miliconfig • انتخاب دستی",
            "outbounds": ["⚡ miliconfig • اتوماتیک (سریع‌ترین)"] + tags + ["direct"]
        }
        urltest_group = {
            "type": "urltest",
            "tag": "⚡ miliconfig • اتوماتیک (سریع‌ترین)",
            "outbounds": tags,
            "url": "https://www.gstatic.com/generate_204",
            "interval": "3m"
        }

        singbox_config = {
            "version": 1,
            "dns": {
                "servers": [
                    {"tag": "dns-remote", "address": "https://1.1.1.1/dns-query", "detour": "🚀 miliconfig • انتخاب دستی"},
                    {"tag": "dns-direct", "address": "223.5.5.5", "detour": "direct"}
                ]
            },
            "inbounds": [
                {"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": 2080}
            ],
            "outbounds": [selector_group, urltest_group] + outbounds + [
                {"type": "direct", "tag": "direct"},
                {"type": "block", "tag": "block"}
            ],
            "route": {
                "rules": [
                    {"geoip": ["private"], "outbound": "direct"},
                    {"geosite": ["cn"], "outbound": "direct"},
                    {"geoip": ["cn"], "outbound": "direct"}
                ],
                "final": "🚀 miliconfig • انتخاب دستی"
            }
        }
        return json.dumps(singbox_config, indent=2, ensure_ascii=False)

    def detect_client_format(self, user_agent: str, target_param: Optional[str] = None) -> str:
        """Detect target subscription format based on parameter or User-Agent."""
        if target_param:
            t = target_param.lower()
            if "clash" in t or "meta" in t or "mihomo" in t:
                return "clash"
            if "sing" in t or "singbox" in t:
                return "singbox"
            if "v2ray" in t or "sub" in t or "base64" in t:
                return "base64"
            if "ss" in t:
                return "base64"

        ua = (user_agent or "").lower()
        if any(c in ua for c in ("clash", "meta", "mihomo", "stash", "flclash")):
            return "clash"
        elif "sing-box" in ua or "singbox" in ua:
            return "singbox"
        elif any(c in ua for c in ("v2ray", "v2rayng", "shadowrocket", "neko")):
            return "base64"
        return "base64"

    def build_subscription(self, token: str, user_agent: str = "", target_param: Optional[str] = None, request_host: Optional[str] = None) -> Tuple[int, str, str]:
        """
        Generate user subscription with dynamic server host resolution.
        """
        user = repo.get_user_by_sub_token(token)
        if not user:
            return 404, "User subscription not found", "text/plain; charset=utf-8"

        access_error = user_access_error(user)
        if access_error:
            return 403, f"Subscription unavailable: {access_error}", "text/plain; charset=utf-8"

        # Resolve the client-facing domain once: a configured unblocked domain must
        # replace the (possibly filtered) host the panel was opened with.
        public_host = self.resolve_public_host(request_host)

        nodes = repo.list_nodes(enabled_only=True)
        nodes = nodes + self.build_gateway_nodes(nodes)
        ss_cred = repo.get_ss_by_user_id(user.id)

        target_format = self.detect_client_format(user_agent, target_param)

        if target_format == "clash":
            content = self.generate_clash_yaml(user, nodes, ss_cred, public_host)
            return 200, content, "text/yaml; charset=utf-8"
        elif target_format == "singbox":
            content = self.generate_singbox_json(user, nodes, ss_cred, public_host)
            return 200, content, "application/json; charset=utf-8"
        else:
            content = self.generate_base64_subscription(user, nodes, ss_cred, public_host)
            return 200, content, "text/plain; charset=utf-8"

subscription_engine = SubscriptionEngine()
