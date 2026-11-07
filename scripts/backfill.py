"""One-off historical import:  python -m scripts.backfill [--start 2000-01-01] [--no-annual]"""
import argparse
from datetime import date

from app.db import make_session_factory
from app.models import SyncRun
from app.sync import run_backfill

p = argparse.ArgumentParser()
p.add_argument("--start", type=date.fromisoformat, help="earliest date to import (live mode needs this)")
p.add_argument("--no-annual", action="store_true")
a = p.parse_args()

sf = make_session_factory()
rid = run_backfill(sf, start=a.start, include_annual=not a.no_annual)
with sf() as s:
    r = s.get(SyncRun, rid)
    print(f"run {r.id}: {r.status} - {r.records_found} found, {r.records_inserted} inserted, {r.records_updated} updated")
    for i in r.items:
        print(f"  {i.source_kind:22} {i.currency_code} {i.status:8} {i.details or i.error_message}")
