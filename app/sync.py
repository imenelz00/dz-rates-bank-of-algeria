"""Daily synchronisation and historical backfill
"""
import threading
from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime

from sqlalchemy import func, select

from . import config
from .annual_import import parse_annual_workbook
from .ingest import UpsertResult, upsert_records
from .models import Currency, ExchangeRate, SyncRun, SyncRunItem, utcnow
from .sources import get_sources

_lock = threading.Lock()


class SyncBusy(Exception):
    pass


def algiers_today() -> date:
    return datetime.now(ZoneInfo(config.TIMEZONE)).date()


@dataclass
class _Outcome:
    source_kind: str
    currency: str
    status: str
    res: UpsertResult | None = None
    error: str | None = None
    details: str | None = None


def _run_item(sf, source_kind, currency, work) -> _Outcome:
    with sf() as s:
        try:
            res, details = work(s)
            s.commit()
            status = "success" if res.rejected == 0 else "partial"
            err = "; ".join(res.errors[:3]) if res.errors else None
            return _Outcome(source_kind, currency, status, res, err, f"{details}. {res.summary()}")
        except Exception as e:  # noqa: BLE001 - every failure must be recorded, not raised
            s.rollback()
            return _Outcome(source_kind, currency, "failed", None, f"{type(e).__name__}: {e}", None)


def _persist(sf, run_id, outcomes: list[_Outcome]):
    with sf() as s:
        run = s.get(SyncRun, run_id)
        for o in outcomes:
            s.add(SyncRunItem(run_id=run_id, source_kind=o.source_kind, currency_code=o.currency, status=o.status,
                              records_found=o.res.found if o.res else 0,
                              records_inserted=o.res.inserted if o.res else 0,
                              records_updated=o.res.updated if o.res else 0,
                              error_message=o.error, details=o.details))
        run.finished_at = utcnow()
        run.records_found = sum(o.res.found for o in outcomes if o.res)
        run.records_inserted = sum(o.res.inserted for o in outcomes if o.res)
        run.records_updated = sum(o.res.updated for o in outcomes if o.res)
        failed = [o for o in outcomes if o.status == "failed"]
        run.status = ("failed" if len(failed) == len(outcomes) and outcomes else
                      "partial" if failed or any(o.status == "partial" for o in outcomes) else "success")
        msgs = [f"{o.source_kind}/{o.currency}: {o.error}" for o in outcomes if o.error]
        run.error_message = "\n".join(msgs) or None
        s.commit()
        return run.status


def _start_run(sf, trigger) -> int:
    with sf() as s:
        run = SyncRun(trigger=trigger, status="running")
        s.add(run)
        s.commit()
        return run.id


def _currencies(sf) -> list[str]:
    with sf() as s:
        return list(s.scalars(select(Currency.code).where(Currency.enabled).order_by(Currency.sort_order)))


def run_sync(sf, *, trigger="scheduled", simulate_fail=(), mode=None, today=None, only=None) -> int:
    """Daily sync. Starts from the last stored date (re-fetching it to catch corrections), so any
    missed days are caught up automatically. `only` = set of (source_kind, currency) to retry."""
    if not _lock.acquire(blocking=False):
        raise SyncBusy("another synchronisation is already running")
    try:
        today = today or algiers_today()
        run_id = _start_run(sf, trigger)
        outcomes = []
        for source in get_sources(mode, simulate_fail):
            for cur in _currencies(sf):
                if only and (source.kind, cur) not in only:
                    continue

                def work(s, source=source, cur=cur):
                    last = s.scalar(select(func.max(ExchangeRate.publication_date)).where(
                        ExchangeRate.currency_code == cur, ExchangeRate.rate_type.in_(source.rate_types),
                        ExchangeRate.granularity == "daily"))
                    start = last or today - timedelta(days=30)
                    gap = (today - last).days if last else None
                    recs = source.fetch(cur, start, today)
                    note = f"fetched {start} to {today}" + (f" (catch-up of {gap} days)" if gap and gap > 4 else "")
                    return upsert_records(s, recs), note

                outcomes.append(_run_item(sf, source.kind, cur, work))
        _persist(sf, run_id, outcomes)
        return run_id
    finally:
        _lock.release()


def retry_failed(sf, run_id, **kw) -> int:
    with sf() as s:
        failed = {(i.source_kind, i.currency_code) for i in s.get(SyncRun, run_id).items if i.status == "failed"}
    if not failed:
        raise ValueError("this run has no failed items")
    return run_sync(sf, trigger="retry", only=failed, **kw)


def run_backfill(sf, *, start: date | None = None, mode=None, simulate_fail=(), include_annual=True, today=None) -> int:
    """Historical import - run once """
    if not _lock.acquire(blocking=False):
        raise SyncBusy("another synchronisation is already running")
    try:
        today = today or algiers_today()
        run_id = _start_run(sf, "backfill")
        outcomes = []
        if include_annual:
            for rate_type, path in config.ANNUAL_FILES.items():
                for cur in _currencies(sf):
                    def work(s, rate_type=rate_type, path=path, cur=cur):
                        if not path.exists():
                            raise FileNotFoundError(path)
                        recs = [r for r in parse_annual_workbook(path, rate_type) if r.currency_code == cur]
                        years = sorted(r.publication_date.year for r in recs)
                        return upsert_records(s, recs), f"{path.name}: earliest year {years[0] if years else 'n/a'}"
                    outcomes.append(_run_item(sf, f"annual:{rate_type}", cur, work))
        for source in get_sources(mode, simulate_fail):
            for cur in _currencies(sf):
                def work(s, source=source, cur=cur):
                    first = start or source.earliest_date(cur)
                    note = f"earliest requested date {first}"
                    if first is None:  # live source without a known archive: take what the page offers now
                        first = today - timedelta(days=30)
                        note = "archive start unknown: imported what the source currently offers"
                    recs: list = []
                    d = first
                    while d <= today:  # yearly chunks keep memory and transactions small
                        end = min(date(d.year, 12, 31), today)
                        recs += source.fetch(cur, d, end)
                        d = end + timedelta(days=1)
                    return upsert_records(s, recs), note
                outcomes.append(_run_item(sf, source.kind, cur, work))
        _persist(sf, run_id, outcomes)
        return run_id
    finally:
        _lock.release()
