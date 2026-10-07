#!/bin/sh
set -e
PORT="${PORT:-8000}"
echo "[BOOT] Starting MILICONFIG on port $PORT..."
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --workers 1 --proxy-headers --forwarded-allow-ips='*'
