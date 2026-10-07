import asyncio
import socket
import struct
import httpx
from typing import Optional, List
from app.config import settings

class DNSResolver:
    def __init__(self, doh_servers: Optional[List[str]] = None):
        self.doh_servers = doh_servers or [
            "https://1.1.1.1/dns-query",
            "https://dns.google/dns-query",
            "https://223.5.5.5/dns-query"
        ]
        self._cache = {}

    async def resolve_doh(self, hostname: str, record_type: str = "A") -> Optional[str]:
        """Resolve a domain using DNS-over-HTTPS (DoH) with JSON query format."""
        if hostname in self._cache:
            return self._cache[hostname]

        headers = {"accept": "application/dns-json"}
        for server in self.doh_servers:
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    resp = await client.get(
                        f"{server}?name={hostname}&type={record_type}",
                        headers=headers
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        answers = data.get("Answer", [])
                        for ans in answers:
                            if ans.get("type") in (1, 28):  # A or AAAA
                                ip = ans.get("data")
                                if ip:
                                    self._cache[hostname] = ip
                                    return ip
            except Exception:
                continue
        return None

    async def resolve(self, hostname: str) -> str:
        """
        Fast asynchronous DNS resolution:
        1. Direct IP check (0ms)
        2. In-memory cache (0ms)
        3. System getaddrinfo (5-15ms)
        4. DoH fallback (if system DNS fails)
        """
        # 1. Check if already an IPv4 or IPv6
        try:
            socket.inet_aton(hostname)
            return hostname
        except Exception:
            pass

        # 2. Check cache
        if hostname in self._cache:
            return self._cache[hostname]

        # 3. High-speed system DNS (fast path on production servers)
        loop = asyncio.get_event_loop()
        try:
            info = await asyncio.wait_for(
                loop.getaddrinfo(hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM),
                timeout=1.5
            )
            if info:
                ip = info[0][4][0]
                self._cache[hostname] = ip
                return ip
        except Exception:
            pass

        # 4. DoH Fallback
        ip = await self.resolve_doh(hostname)
        if ip:
            return ip

        return hostname

dns_resolver = DNSResolver(settings.dns_server_list)
