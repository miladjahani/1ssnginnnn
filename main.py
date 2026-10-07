import os
import sys
import importlib.util

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

# Ensure app package is registered in sys.modules
if "app" not in sys.modules:
    app_dir = os.path.join(CURRENT_DIR, "app")
    spec = importlib.util.spec_from_file_location("app", os.path.join(app_dir, "__init__.py"), submodule_search_locations=[app_dir])
    app_mod = importlib.util.module_from_spec(spec)
    sys.modules["app"] = app_mod
    spec.loader.exec_module(app_mod)

import uvicorn

if __name__ == "__main__":
    raw_port = str(os.environ.get("PORT", "8000")).strip()
    try:
        port = int(raw_port)
    except Exception:
        port = 8000
    host = "0.0.0.0"
    print(f"[*] Starting MILICONFIG on {host}:{port} ...", flush=True)
    uvicorn.run("app.main:app", host=host, port=port, proxy_headers=True, forwarded_allow_ips="*")
