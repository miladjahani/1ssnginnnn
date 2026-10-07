import unittest
import uuid
import os
from unittest import mock

from app.database import db
from app.services.repository import repo
from app.services.access import user_access_error, parse_expiry
from app.subscriptions.engine import subscription_engine
from app.config import settings


class TestAccessEnforcement(unittest.TestCase):
    def setUp(self):
        db.init_schema()

    def test_status_expiry_and_quota_rules(self):
        self.assertIsNone(user_access_error(repo.create_user(f"ok_{uuid.uuid4().hex[:8]}", "OK")))
        self.assertIn("expired", user_access_error(repo.create_user(
            f"exp_{uuid.uuid4().hex[:8]}", "Expired", expires_at="2020-01-01")))
        self.assertIsNone(user_access_error(repo.create_user(
            f"fut_{uuid.uuid4().hex[:8]}", "Future", expires_at="2999-01-01T00:00:00")))
        self.assertEqual(user_access_error(None), "user not found")

    def test_expired_user_subscription_is_refused(self):
        user = repo.create_user(
            f"subexp_{uuid.uuid4().hex[:8]}", "Expired Sub", expires_at="2020-05-05"
        )
        status, content, _ = subscription_engine.build_subscription(token=user.subscription_token)
        self.assertEqual(status, 403)
        self.assertIn("expired", content)

    def test_quota_exhausted_subscription_is_refused(self):
        user = repo.create_user(
            f"subquota_{uuid.uuid4().hex[:8]}", "Quota Sub", traffic_limit=1000
        )
        repo.record_user_traffic(user.id, upload_bytes=600, download_bytes=500)
        status, content, _ = subscription_engine.build_subscription(token=user.subscription_token)
        self.assertEqual(status, 403)
        self.assertIn("quota", content)

    def test_user_within_quota_still_served(self):
        user = repo.create_user(
            f"subok_{uuid.uuid4().hex[:8]}", "Quota OK", traffic_limit=1000
        )
        repo.record_user_traffic(user.id, upload_bytes=100, download_bytes=100)
        status, content, _ = subscription_engine.build_subscription(token=user.subscription_token)
        self.assertEqual(status, 200)

    def test_unknown_token_still_404(self):
        status, _, _ = subscription_engine.build_subscription(token="does-not-exist")
        self.assertEqual(status, 404)

    def test_parse_expiry_formats(self):
        self.assertIsNone(parse_expiry(None))
        self.assertIsNone(parse_expiry(""))
        self.assertIsNone(parse_expiry("never"))
        self.assertIsNotNone(parse_expiry("2027-01-01"))
        self.assertIsNotNone(parse_expiry("2027-01-01T10:20:30Z"))
        self.assertIsNotNone(parse_expiry("1893456000"))  # epoch seconds (2030)
        self.assertIsNone(parse_expiry("definitely-not-a-date"))


class TestShadowSocksPublishing(unittest.TestCase):
    def setUp(self):
        db.init_schema()
        self.user = repo.create_user(f"sspub_{uuid.uuid4().hex[:8]}", "SS Publish")
        self.cred = repo.create_or_update_ss(self.user.id, password="pub_secret", port=8388)

    def test_local_endpoint_is_published_off_railway(self):
        railway_keys = {k: v for k, v in os.environ.items() if k.startswith("RAILWAY_")}
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in railway_keys:
                os.environ.pop(k, None)
            self.assertFalse(settings.running_on_railway)
            self.assertTrue(subscription_engine.is_ss_publishable(self.cred))

    def test_local_endpoint_hidden_on_railway_without_tcp_proxy(self):
        with mock.patch.dict(os.environ, {"RAILWAY_ENVIRONMENT_NAME": "production"}, clear=False):
            self.assertFalse(subscription_engine.is_ss_publishable(self.cred))
            status, content, _ = subscription_engine.build_subscription(
                token=self.user.subscription_token, target_param="base64"
            )
            self.assertEqual(status, 200)
            import base64 as b64
            decoded = b64.b64decode(content).decode("utf-8")
            self.assertFalse(any(line.startswith("ss://") for line in decoded.splitlines()))

    def test_explicit_public_endpoint_is_used(self):
        with mock.patch.object(settings, "SS_PUBLIC_HOST", "tcp.example.net"), \
             mock.patch.object(settings, "SS_PUBLIC_PORT", 21999):
            self.assertTrue(subscription_engine.is_ss_publishable(self.cred))
            host, port = subscription_engine.resolve_ss_endpoint(self.cred, "panel.example.com")
            self.assertEqual((host, port), ("tcp.example.net", 21999))
            status, content, _ = subscription_engine.build_subscription(
                token=self.user.subscription_token, target_param="base64"
            )
            import base64 as b64
            decoded = b64.b64decode(content).decode("utf-8")
            ss_lines = [line for line in decoded.splitlines() if line.startswith("ss://")]
            self.assertEqual(len(ss_lines), 1)
            self.assertIn("tcp.example.net:21999", ss_lines[0])

    def test_ss_port_default_follows_settings(self):
        self.assertEqual(int(self.cred.port), settings.SS_PORT)


if __name__ == "__main__":
    unittest.main()
