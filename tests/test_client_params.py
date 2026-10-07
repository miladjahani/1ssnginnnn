"""ALPN / fingerprint / fragment and real subscription headers (StanNG-inspired)."""
import base64
import json
import unittest
import urllib.parse
import uuid

import yaml

from app.database import db
from app.services.repository import repo
from app.subscriptions.engine import subscription_engine
from app.api.subscriptions import build_subscription_headers, _epoch_seconds


class TestAlpnDefaults(unittest.TestCase):
    def setUp(self):
        db.init_schema()
        tag = uuid.uuid4().hex[:6]
        self.user = repo.create_user(f"params_{tag}", "Params")
        self.ws = repo.create_node(
            name=f"miliconfig • WS {tag}", protocol="vless", address=f"ws-{tag}.example.org",
            port=443, network="ws", tls=True, path="/?ed=2048",
        )
        self.xhttp = repo.create_node(
            name=f"miliconfig • XH {tag}", protocol="vless", address=f"xh-{tag}.example.org",
            port=443, network="xhttp", tls=True, path="/xhttp",
        )
        for key in ("default_alpn", "default_fingerprint"):
            repo.set_setting(key, "")
        repo.set_setting("fragment_enabled", "true")
        repo.set_setting("fragment_length", "")
        repo.set_setting("fragment_interval", "")

    def uris(self):
        token = self.user.subscription_token
        _, content, _ = subscription_engine.build_subscription(
            token=token, target_param="base64", request_host="panel.example.org"
        )
        return base64.b64decode(content).decode("utf-8").splitlines()

    def uri_for(self, address):
        return [l for l in self.uris() if f"@{address}:443" in l][0]

    def params(self, uri):
        return dict(urllib.parse.parse_qsl(uri.split("?", 1)[1].split("#", 1)[0]))

    def test_websocket_defaults_to_http11_alpn(self):
        p = self.params(self.uri_for(self.ws.address))
        self.assertEqual(p["alpn"], "http/1.1")
        self.assertNotIn("h2", p["alpn"], "h2 first breaks the WebSocket upgrade on HTTP/2 edges")

    def test_xhttp_defaults_to_h2_alpn(self):
        p = self.params(self.uri_for(self.xhttp.address))
        self.assertEqual(p["alpn"], "h2")

    def test_node_alpn_overrides_default(self):
        repo.update_node(self.ws.id, alpn="http/1.1,h2")
        p = self.params(self.uri_for(self.ws.address))
        self.assertEqual(p["alpn"], "http/1.1,h2")

    def test_global_alpn_setting_overrides_transport_default(self):
        repo.set_setting("default_alpn", "http/1.1")
        self.assertEqual(self.params(self.uri_for(self.ws.address))["alpn"], "http/1.1")

    def test_fingerprint_setting_is_used_and_validated(self):
        repo.set_setting("default_fingerprint", "randomized")
        self.assertIn("fp=randomized", self.uri_for(self.ws.address))
        repo.set_setting("default_fingerprint", "not-a-fingerprint")
        self.assertIn("fp=chrome", self.uri_for(self.ws.address))

    def test_clash_and_singbox_carry_alpn_and_fingerprint(self):
        token = self.user.subscription_token
        _, clash, _ = subscription_engine.build_subscription(token=token, target_param="clash")
        proxies = [p for p in yaml.safe_load(clash)["proxies"] if p.get("server") == self.ws.address]
        self.assertEqual(proxies[0]["alpn"], ["http/1.1"])
        self.assertEqual(proxies[0]["client-fingerprint"], "chrome")

        _, singbox, _ = subscription_engine.build_subscription(token=token, target_param="singbox")
        outbounds = json.loads(singbox)["outbounds"]
        ob = [o for o in outbounds if o.get("server") == self.ws.address][0]
        self.assertEqual(ob["tls"]["alpn"], ["http/1.1"])
        self.assertEqual(ob["tls"]["utls"]["fingerprint"], "chrome")

    def test_fragment_settings_reach_the_anti_filter_node(self):
        tag = uuid.uuid4().hex[:6]
        node = repo.create_node(
            name=f"miliconfig • ⚡ ریل‌وی ضد فیلتر {tag}", protocol="vless",
            address=f"frag-{tag}.example.org", port=443, network="ws", tls=True,
        )
        repo.set_setting("fragment_length", "10-30")
        repo.set_setting("fragment_interval", "10-20")
        _, singbox, _ = subscription_engine.build_subscription(
            token=self.user.subscription_token, target_param="singbox"
        )
        ob = [o for o in json.loads(singbox)["outbounds"] if o.get("server") == node.address][0]
        self.assertEqual(ob["tls"]["fragment"], {"size": "10-30", "sleep": "10-20", "enabled": True})

    def test_fragment_can_be_disabled(self):
        repo.set_setting("fragment_enabled", "false")
        self.assertIsNone(subscription_engine.fragment_settings())


class TestSubscriptionUserinfoHeaders(unittest.TestCase):
    def setUp(self):
        db.init_schema()
        tag = uuid.uuid4().hex[:6]
        self.user = repo.create_user(
            f"info_{tag}", "Info User", traffic_limit=10 * 1024 ** 3,
            expires_at="2030-01-01T00:00:00",
        )

    def header_map(self):
        headers = build_subscription_headers(self.user.subscription_token)
        return {k.lower(): v for k, v in headers.items()}

    def test_userinfo_reports_real_usage(self):
        repo.record_user_traffic(self.user.id, upload_bytes=1234, download_bytes=5678)
        info = self.header_map()["subscription-userinfo"]
        self.assertIn("upload=1234", info)
        self.assertIn("download=5678", info)
        self.assertIn(f"total={10 * 1024 ** 3}", info)
        self.assertNotIn("total=107374182400", info, "the old fake 100GB total must be gone")
        self.assertNotEqual(info.split("expire=")[1], "0", "expiry must be a real timestamp")

    def test_unlimited_user_reports_zero_total(self):
        user = repo.create_user(f"unlim_{uuid.uuid4().hex[:6]}", "Unlimited")
        headers = {k.lower(): v for k, v in build_subscription_headers(user.subscription_token).items()}
        self.assertIn("total=0", headers["subscription-userinfo"])
        self.assertIn("expire=0", headers["subscription-userinfo"])

    def test_profile_title_and_update_interval(self):
        headers = self.header_map()
        self.assertTrue(headers["profile-title"].startswith("base64:"))
        decoded = base64.b64decode(headers["profile-title"].split("base64:")[1]).decode()
        self.assertIn("MILICONFIG", decoded)
        self.assertEqual(headers["profile-update-interval"], "1")
        self.assertIn("no-store", headers["cache-control"])

    def test_userinfo_is_sent_once(self):
        headers = build_subscription_headers(self.user.subscription_token)
        names = [k.lower() for k in headers]
        self.assertEqual(names.count("subscription-userinfo"), 1,
                         "a duplicated header key is emitted twice on the wire")

    def test_unknown_token_returns_safe_headers(self):
        headers = build_subscription_headers("no-such-token")
        self.assertNotIn("Subscription-Userinfo", headers)

    def test_epoch_conversion(self):
        self.assertEqual(_epoch_seconds(None), 0)
        self.assertEqual(_epoch_seconds(""), 0)
        self.assertEqual(_epoch_seconds("1970-01-01T00:00:00"), 0)
        self.assertEqual(_epoch_seconds("2030-01-01T00:00:00"), 1893456000)


if __name__ == "__main__":
    unittest.main()
