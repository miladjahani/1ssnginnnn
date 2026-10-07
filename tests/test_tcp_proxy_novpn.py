"""Tests for the no-domain path: Railway TCP proxy + plain (no-TLS) WebSocket nodes."""
import base64
import json
import os
import unittest
import uuid
from unittest import mock

import yaml

from app.database import db
from app.services.repository import repo
from app.services import bootstrap
from app.subscriptions.engine import subscription_engine


class TestTcpProxySeeding(unittest.TestCase):
    def setUp(self):
        db.init_schema()

    def test_no_endpoint_means_no_nodes(self):
        with mock.patch.object(bootstrap.settings, "TCP_PROXY_HOST", ""), \
             mock.patch.object(bootstrap.settings, "TCP_PROXY_PORT", 0), \
             mock.patch.object(bootstrap.settings, "RAILWAY_TCP_PROXY_DOMAIN", ""), \
             mock.patch.object(bootstrap.settings, "RAILWAY_TCP_PROXY_PORT", 0):
            self.assertIsNone(bootstrap.tcp_proxy_endpoint())
            self.assertEqual(bootstrap.seed_railway_tcp_proxy_nodes(), [])

    def test_endpoint_creates_plain_ws_nodes_and_is_idempotent(self):
        endpoint = ("tcp-proxy-example.rlwy.net", 29461)
        created = bootstrap.seed_railway_tcp_proxy_nodes(endpoint=endpoint)
        self.assertEqual(len(created), 2)
        protocols = sorted(n.protocol for n in created)
        self.assertEqual(protocols, ["trojan", "vless"])
        for node in created:
            self.assertEqual(node.address, endpoint[0])
            self.assertEqual(int(node.port), endpoint[1])
            self.assertEqual(int(node.tls), 0, "TCP proxy nodes must be plain (no TLS)")
            self.assertEqual(node.network, "ws")
            self.assertTrue(node.name.startswith("miliconfig"))
        # second run must not add duplicates
        self.assertEqual(bootstrap.seed_railway_tcp_proxy_nodes(endpoint=endpoint), [])
        matching = [n for n in repo.list_nodes()
                    if n.address == endpoint[0] and int(n.port) == endpoint[1]]
        self.assertEqual(len(matching), 2)

    def test_endpoint_from_railway_env_when_proxy_targets_web_port(self):
        with mock.patch.object(bootstrap.settings, "TCP_PROXY_HOST", ""), \
             mock.patch.object(bootstrap.settings, "TCP_PROXY_PORT", 0), \
             mock.patch.object(bootstrap.settings, "RAILWAY_TCP_PROXY_DOMAIN", "edge.rlwy.net"), \
             mock.patch.object(bootstrap.settings, "RAILWAY_TCP_PROXY_PORT", 12345), \
             mock.patch.object(bootstrap.settings, "RAILWAY_TCP_APPLICATION_PORT", bootstrap.settings.PORT):
            self.assertEqual(bootstrap.tcp_proxy_endpoint(), ("edge.rlwy.net", 12345))

    def test_endpoint_ignored_when_proxy_targets_shadowsocks(self):
        with mock.patch.object(bootstrap.settings, "TCP_PROXY_HOST", ""), \
             mock.patch.object(bootstrap.settings, "TCP_PROXY_PORT", 0), \
             mock.patch.object(bootstrap.settings, "RAILWAY_TCP_PROXY_DOMAIN", "edge.rlwy.net"), \
             mock.patch.object(bootstrap.settings, "RAILWAY_TCP_PROXY_PORT", 12345), \
             mock.patch.object(bootstrap.settings, "SS_PORT", 12345), \
             mock.patch.object(bootstrap.settings, "RAILWAY_TCP_APPLICATION_PORT", 12345):
            # the raw/TCP ingress serves ShadowSocks, not the panel: no plain WS nodes
            self.assertIsNone(bootstrap.tcp_proxy_endpoint())
            self.assertEqual(bootstrap.settings.ss_public_endpoint, ("edge.rlwy.net", 12345))


class TestPlainTransportConfigs(unittest.TestCase):
    """A filtered HTTPS hostname must not be required: plain ws:// configs are emitted."""

    def setUp(self):
        db.init_schema()
        tag = uuid.uuid4().hex[:6]
        self.domain, self.port = f"tcp-proxy-{tag}.rlwy.net", 29461
        self.user = repo.create_user(f"plain_{tag}", "Plain")
        self.vless = repo.create_node(
            name=f"miliconfig • 🔓 ریل‌وی بدون TLS {tag}", protocol="vless",
            address=self.domain, port=self.port, network="ws", tls=False,
            path="/?ed=2048", region="Railway-TCP",
        )
        self.trojan = repo.create_node(
            name=f"miliconfig • 🔓 تروجان بدون TLS {tag}", protocol="trojan",
            address=self.domain, port=self.port, network="ws", tls=False,
            path="/?ed=2048", region="Railway-TCP",
        )

    def own_lines(self, target="base64"):
        status, content, _ = subscription_engine.build_subscription(
            token=self.user.subscription_token, target_param=target, request_host="railway.app"
        )
        self.assertEqual(status, 200)
        if target == "base64":
            content = base64.b64decode(content).decode("utf-8")
        return status, content

    def test_vless_and_trojan_plain_uris(self):
        _, content = self.own_lines()
        lines = content.splitlines()
        vless = [l for l in lines if l.startswith("vless://") and f"@{self.domain}:{self.port}" in l]
        trojan = [l for l in lines if l.startswith("trojan://") and f"@{self.domain}:{self.port}" in l]
        self.assertEqual(len(vless), 1)
        self.assertEqual(len(trojan), 1)
        for line in vless + trojan:
            self.assertIn("security=none", line)
            self.assertIn("type=ws", line)
            self.assertNotIn("sni=", line, "plain transport must not advertise SNI")
            self.assertNotIn("fp=chrome", line, "TLS fingerprint is meaningless without TLS")
            self.assertNotIn("alpn=", line)

    def test_clash_plain_proxy(self):
        _, content = self.own_lines("clash")
        proxies = yaml.safe_load(content)["proxies"]
        plain = [p for p in proxies if p.get("server") == self.domain and int(p.get("port")) == self.port]
        self.assertEqual(len(plain), 2)
        for p in plain:
            self.assertFalse(p["tls"])
            self.assertNotIn("servername", p)
            self.assertNotIn("client-fingerprint", p)
            self.assertTrue(p["ws-opts"]["path"].startswith("/"))

    def test_singbox_plain_outbound(self):
        _, content = self.own_lines("singbox")
        outbounds = json.loads(content)["outbounds"]
        plain = [o for o in outbounds if o.get("server") == self.domain]
        self.assertEqual(len(plain), 2)
        for ob in plain:
            self.assertNotIn("tls", ob)

    def test_tls_nodes_still_emit_tls_fields(self):
        tag = uuid.uuid4().hex[:6]
        tls_node = repo.create_node(
            name=f"miliconfig • TLS {tag}", protocol="vless", address=f"tls-{tag}.example.org",
            port=443, network="ws", tls=True, path="/?ed=2048",
        )
        _, content = self.own_lines()
        lines = [l for l in content.splitlines() if f"@{tls_node.address}:443" in l]
        self.assertEqual(len(lines), 1)
        self.assertIn("security=tls", lines[0])
        self.assertIn("sni=", lines[0])
        self.assertIn("fp=chrome", lines[0])

    def test_proxy_host_header_keeps_public_domain(self):
        """Plain transport still needs a Host header for the WS handshake."""
        repo.set_setting("public_base_url", "panel.mydomain.ir")
        _, content = self.own_lines()
        line = [l for l in content.splitlines() if f"@{self.domain}:{self.port}" in l][0]
        self.assertIn("host=panel.mydomain.ir", line)


if __name__ == "__main__":
    unittest.main()
