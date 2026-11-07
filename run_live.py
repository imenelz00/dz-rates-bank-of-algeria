"""One-command live mode.   python run_live.py probe   -> look at the real pages once
                              python run_live.py         -> start the app on http://localhost:8000
Sets the permission + certificate workarounds for you (see README before using them elsewhere)."""
import os
import runpy
import sys
from pathlib import Path

os.environ.setdefault("BOA_PERMISSION_GRANTED", "1")  
os.environ.setdefault("BOA_INSECURE_TLS", "1")        
os.environ["SOURCE_MODE"] = "live"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(__file__).resolve().parent / 'data' / 'rates_live.db'}")  # keeps real data apart from demo data

if sys.argv[1:] == ["probe"]:
    runpy.run_module("scripts.probe", run_name="__main__")
else:
    import uvicorn
    uvicorn.run("app.main:app_factory", factory=True, port=8000)
