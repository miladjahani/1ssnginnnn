"""Tests for the ISP-proof public routing (custom domain / gateway + clean IPs)."""
import base64
import json
import unittest
import uuid
from unittest import mock
import os

import yaml
import urllib.parse

from app.database import db
from app.services.repository import repo
from app.subscriptions.engine import subscription_engine


class UnblockedRoutingTestBase(unittest.TestCase):
    def setUp(self):
        db.init_schema()
        tag = uuid.uuid4().hex[:6]
        self.user = repo.create_user(f"unblocked_{tag}", "Unblocked")
        self.vless = repo.create_node(
            name=f"miliconfig • 🚂 ریل‌وی مستقیم {tag}", protocol="vless", address="",
            port=443, network="ws", tls=True, path="/?ed=2048", region="Railway",
        )
        self.trojan = repo.create_node(
            name=f"miliconfig • 🔒 تروجان ریل‌وی {tag}", protocol="trojan", address="",
            port=443, network="ws", tls=True, path="/?ed=2048", region="Railway",
        )
        # isolate the settings used by this test case
        for key in ("public_base_url", "gateway_domain", "clean_ips"):
            repo.set_setting(key, "")

    def own_lines(self, request_host):
        """Base64/config lines belonging to the two nodes created by this test case.

        The suite shares a single SQLite file, so assertions must ignore nodes that
        other test modules created.
        """
        return [l for l in self.b64_lines(request_host) if self._is_local_node(l)]

    def _is_local_node(self, line):
        if "#" not in line:
            return False
        tag = urllib.parse.unquote(line.split("#", 1)[1])
        return any(tag.startswith(name) for name in (self.vless.name, self.trojan.name))

    def own_clash_proxies(self, request_host):
        proxies = self.clash_proxies(request_host)
        return [p for p in proxies
                if any(p["name"].startswith(name) for name in (self.vless.name, self.trojan.name))]

    def b64_lines(self, request_host):
        status, content, ctype = subscription_engine.build_subscription(
            token=self.user.subscription_token, target_param="base64", request_host=request_host
        )
        self.assertEqual(status, 200)
        return base64.b64decode(content).decode("utf-8").splitlines()

    def clash_proxies(self, request_host):
        status, content, _ = subscription_engine.build_subscription(
            token=self.user.subscription_token, target_param="clash", request_host=request_host
        )
        self.assertEqual(status, 200)
        return yaml.safe_load(content)["proxies"]


class TestPublicDomainOverride(UnblockedRoutingTestBase):
    def test_request_host_used_when_nothing_configured(self):
        lines = self.own_lines("milinewc2-production.up.railway.app")
        self.assertEqual(len(lines), 2)
        self.assertTrue(all("milinewc2-production.up.railway.app" in l for l in lines))
        self.assertFalse(any("panel.mydomain.ir" in l for l in lines))

    def test_configured_public_base_url_replaces_blocked_request_host(self):
        repo.set_setting("public_base_url", "https://panel.mydomain.ir")
        lines = self.own_lines("milinewc2-production.up.railway.app")
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertIn("panel.mydomain.ir", line)
            self.assertNotIn("railway.app", line)

    def test_env_public_base_url_is_honoured(self):
        with mock.patch.dict(os.environ, {"PUBLIC_BASE_URL": "https://env.example.org"}, clear=False):
            lines = self.own_lines("milinewc2-production.up.railway.app")
        self.assertEqual(len(lines), 2)
        self.assertTrue(all("env.example.org" in l for l in lines))

    def test_panel_setting_wins_over_environment(self):
        repo.set_setting("public_base_url", "panel.mydomain.ir")
        with mock.patch.dict(os.environ, {"PUBLIC_BASE_URL": "https://env.example.org"}, clear=False):
            self.assertEqual(subscription_engine.resolve_public_host("railway.app"), "panel.mydomain.ir")

    def test_clash_and_singbox_use_the_public_domain_too(self):
        repo.set_setting("public_base_url", "panel.mydomain.ir")
        served = self.own_clash_proxies("milinewc2-production.up.railway.app")
        self.assertEqual(len(served), 2, [p["name"] for p in served])
        self.assertTrue(all(p["server"] == "panel.mydomain.ir" for p in served))
        self.assertTrue(all(p.get("servername") == "panel.mydomain.ir" for p in served))

    def test_hostname_parsing(self):
        self.assertEqual(subscription_engine.hostname_of("https://a.b.com:8443/x?y=1"), "a.b.com")
        self.assertEqual(subscription_engine.hostname_of("a.b.com:443"), "a.b.com")
        self.assertEqual(subscription_engine.hostname_of("a.b.com"), "a.b.com")
        self.assertEqual(subscription_engine.hostname_of(""), "")


class TestCleanIpGatewayNodes(UnblockedRoutingTestBase):
    def test_clean_ip_nodes_use_ip_address_with_gateway_sni(self):
        repo.set_setting("public_base_url", "panel.mydomain.ir")
        repo.set_setting("gateway_domain", "panel.mydomain.ir")
        repo.set_setting("clean_ips", "104.16.132.229, 104.17.147.22")

        lines = self.b64_lines("milinewc2-production.up.railway.app")
        clean = [l for l in lines if "@104." in l]
        self.assertEqual(len(clean), 4)  # 2 protocols x 2 clean IPs
        for line in clean:
            self.assertIn("sni=panel.mydomain.ir", line)
            self.assertIn("host=panel.mydomain.ir", line)
            self.assertIn("security=tls", line)
        self.assertTrue(all(l.startswith("miliconfig") is False for l in clean))  # names live in the tag
        tags = [l.split("#", 1)[1] for l in clean if "#" in l]
        self.assertTrue(all(t.lower().startswith("miliconfig") for t in tags))
        self.assertTrue(all("railway.app" not in l for l in clean))

        proxies = self.clash_proxies("milinewc2-production.up.railway.app")
        clean_proxies = [p for p in proxies if p["server"].startswith("104.")]
        self.assertEqual(len(clean_proxies), 4)
        for p in clean_proxies:
            self.assertEqual(p["servername"], "panel.mydomain.ir")
            self.assertTrue(p["name"].startswith("miliconfig"))

    def test_clean_ips_fall_back_to_proxyip_pool(self):
        repo.set_setting("gateway_domain", "panel.mydomain.ir")
        repo.set_setting("clean_ips", "")
        pip = repo.add_proxy_ip("198.51.100.7", port=443, region="CF")
        try:
            endpoints = subscription_engine.clean_ip_endpoints(limit=10)
            self.assertIn("198.51.100.7", endpoints)
            variants = subscription_engine.build_gateway_nodes([self.vless])
            addresses = {n.address for n in variants}
            self.assertTrue(addresses.issubset(set(endpoints)))
            self.assertGreaterEqual(len(addresses), 1)
        finally:
            repo.delete_proxy_ip(pip.id)

    def test_clean_ip_limit_is_respected(self):
        repo.set_setting("gateway_domain", "panel.mydomain.ir")
        repo.set_setting("clean_ips", "1.1.1.1,2.2.2.2,3.3.3.3")
        self.assertEqual(subscription_engine.clean_ip_endpoints(), ["1.1.1.1", "2.2.2.2"])
        self.assertEqual(subscription_engine.clean_ip_endpoints(limit=3), ["1.1.1.1", "2.2.2.2", "3.3.3.3"])

    def test_no_gateway_domain_means_no_variants(self):
        repo.set_setting("clean_ips", "104.16.132.229")
        lines = self.b64_lines("milinewc2-production.up.railway.app")
        self.assertFalse(any("@104.16.132.229" in l for l in lines))

    def test_gateway_variants_reachable_in_singbox_json(self):
        repo.set_setting("gateway_domain", "panel.mydomain.ir")
        repo.set_setting("clean_ips", "104.16.132.229")
        status, content, _ = subscription_engine.build_subscription(
            token=self.user.subscription_token, target_param="singbox", request_host="railway.app"
        )
        config = json.loads(content)
        variants = [o for o in config["outbounds"] if o.get("server") == "104.16.132.229"]
        self.assertEqual(len(variants), 2)
        for ob in variants:
            self.assertEqual(ob["tls"]["server_name"], "panel.mydomain.ir")


if __name__ == "__main__":
    unittest.main()
