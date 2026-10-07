#!/bin/sh
set -e

PORT="${PORT:-8000}"
echo "[*] Launching MILICONFIG on port ${PORT}..."
exec python3 main.py
