"""Railway deployment contract tests: env parsing, PORT binding and persistence."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

INSPECT_SNIPPET = """
import json, sys
sys.path.insert(0, '.')
from app.config import settings
from app.database import get_writable_db_path
print(json.dumps({
    "port": settings.PORT,
    "ss_port": settings.SS_PORT,
    "ss_public_endpoint": list(settings.ss_public_endpoint),
    "db_path": get_writable_db_path(),
    "running_on_railway": bool(settings.running_on_railway),
}))
"""


def inspect(env_overrides):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PORT", "SS_", "DATABASE_URL", "DATA_DIR", "RAILWAY_"))}
    env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, "-c", INSPECT_SNIPPET],
        cwd=ROOT_DIR, env=env, capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"inspection failed:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestRailwayDeployment(unittest.TestCase):
    def test_railway_port_and_tcp_proxy_env(self):
        with tempfile.TemporaryDirectory() as data_dir:
            info = inspect({
                "PORT": "9032",
                "RAILWAY_ENVIRONMENT_NAME": "production",
                "RAILWAY_TCP_APPLICATION_PORT": "8443",
                "RAILWAY_TCP_PROXY_DOMAIN": "interchange.proxy.rlwy.net",
                "RAILWAY_TCP_PROXY_PORT": "19123",
                "DATA_DIR": data_dir,
            })
            self.assertEqual(info["port"], 9032)
            self.assertEqual(info["ss_port"], 8443)
            self.assertEqual(info["ss_public_endpoint"], ["interchange.proxy.rlwy.net", 19123])
            self.assertTrue(info["running_on_railway"])
            self.assertEqual(info["db_path"], os.path.join(data_dir, "miliconfig.db"))

    def test_invalid_port_falls_back_without_crashing(self):
        info = inspect({"PORT": "not-a-number", "SS_PORT": ""})
        self.assertEqual(info["port"], 8000)
        self.assertGreater(info["ss_port"], 0)

    def test_postgres_url_does_not_break_startup(self):
        with tempfile.TemporaryDirectory() as data_dir:
            info = inspect({
                "DATABASE_URL": "postgresql://user:pass@db.railway.internal:5432/railway",
                "DATA_DIR": data_dir,
            })
            self.assertTrue(info["db_path"].startswith(data_dir))

    def test_explicit_sqlite_url_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "explicit.db")
            info = inspect({
                "DATABASE_URL": f"sqlite:////{target.lstrip('/')}",
                "DATA_DIR": os.path.join(tmp, "ignored-data-dir"),
            })
            self.assertEqual(info["db_path"], target)

    def test_persistent_volume_preferred_over_container_fs(self):
        with tempfile.TemporaryDirectory() as data_dir:
            info = inspect({"DATA_DIR": data_dir})
            self.assertEqual(info["db_path"], os.path.join(data_dir, "miliconfig.db"))


if __name__ == "__main__":
    unittest.main()


class TestLogLevelHandling(unittest.TestCase):
    def test_lowercase_log_level_does_not_crash_import(self):
        for value in ("info", "debug", "WARNING", "30", "verbose-nonsense", ""):
            proc = subprocess.run(
                [sys.executable, "-c", "import sys; sys.path.insert(0,'.'); import app.main; print('IMPORT_OK')"],
                cwd=ROOT_DIR,
                env={**os.environ, "LOG_LEVEL": value},
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(proc.returncode, 0, f"LOG_LEVEL={value!r} crashed:\n{proc.stderr}")
            self.assertIn("IMPORT_OK", proc.stdout)
