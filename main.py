"""Miliconfig process entrypoint.

Boots the FastAPI application with uvicorn bound to the port provided by the
platform ($PORT on Railway) or 8000 locally.
"""
import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

import uvicorn  # noqa: E402  (import after sys.path bootstrap)


def _listen_port() -> int:
    try:
        return int(str(os.environ.get("PORT", "8000")).strip())
    except Exception:
        return 8000


if __name__ == "__main__":
    port = _listen_port()
    host = "0.0.0.0"
    print(f"[*] Starting MILICONFIG on {host}:{port} ...", flush=True)
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level=(os.environ.get("LOG_LEVEL") or "info").strip().lower(),
    )
