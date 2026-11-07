import csv
import io
import threading
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from . import config
from .db import make_session_factory
from .models import Currency, ExchangeRate, SyncRun
from .scheduler import start_scheduler
from .sync import SyncBusy, retry_failed, run_backfill, run_sync

DEMO = lambda: config.SOURCE_MODE != "live"  # noqa: E731


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def create_app(db_url: str | None = None, *, background: bool = True) -> FastAPI:
    sf = make_session_factory(db_url)

    @asynccontextmanager
    async def lifespan(app):
        sched = None
        if background:
            with sf() as s:
                empty = s.scalar(select(func.count(ExchangeRate.id))) == 0
            if empty and config.AUTO_BACKFILL:
                run_backfill(sf)
            if config.SCHEDULER_ENABLED:
                sched = start_scheduler(sf)
        yield
        if sched:
            sched.shutdown(wait=False)

    app = FastAPI(title="Bank of Algeria rates centraliser (mock)", lifespan=lifespan)
    app.state.sf = sf

    def filters(stmt, currency, types, start, end):
        if currency:
            stmt = stmt.where(ExchangeRate.currency_code == currency.upper())
        if types:
            stmt = stmt.where(ExchangeRate.rate_type.in_([t for t in types.split(",") if t]))
        if start:
            stmt = stmt.where(ExchangeRate.publication_date >= start)
        if end:
            stmt = stmt.where(ExchangeRate.publication_date <= end)
        return stmt

    def row(r: ExchangeRate):
        return {"id": r.id, "currency": r.currency_code, "rate_type": r.rate_type, "granularity": r.granularity,
                "value": float(r.value), "publication_date": r.publication_date.isoformat(),
                "value_date": r.value_date.isoformat(), "retrieved_at": _iso(r.retrieved_at),
                "source_kind": r.source_kind, "flagged": r.flagged, "flag_reason": r.flag_reason}

    # ---- meta / status ------------------------------------------------------------------------
    @app.get("/api/meta")
    def meta():
        with sf() as s:
            cur = [{"code": c.code, "name": c.name} for c in s.scalars(
                select(Currency).where(Currency.enabled).order_by(Currency.sort_order))]
            rng = s.execute(select(ExchangeRate.rate_type, func.min(ExchangeRate.publication_date),
                                   func.max(ExchangeRate.publication_date), func.count())
                            .group_by(ExchangeRate.rate_type)).all()
            runs = list(s.scalars(select(SyncRun).where(SyncRun.trigger != "backfill")
                                  .order_by(SyncRun.id.desc()).limit(config.ALERT_AFTER_FAILURES)))
            streak = 0
            for r in runs:
                if r.status == "failed":
                    streak += 1
                else:
                    break
            return {"mode": config.SOURCE_MODE, "demo": DEMO(), "sync_time": config.SYNC_TIME,
                    "timezone": config.TIMEZONE, "currencies": cur,
                    "coverage": {t: {"from": a.isoformat(), "to": b.isoformat(), "count": n} for t, a, b, n in rng},
                    "alert": streak >= config.ALERT_AFTER_FAILURES, "failure_streak": streak}

    @app.get("/api/current")
    def current():
        with sf() as s:
            ok = s.scalars(select(SyncRun).where(SyncRun.trigger != "backfill", SyncRun.status == "success")
                           .order_by(SyncRun.id.desc()).limit(1)).first()
            last = s.scalars(select(SyncRun).where(SyncRun.trigger != "backfill")
                             .order_by(SyncRun.id.desc()).limit(1)).first()
            out = []
            for c in s.scalars(select(Currency).where(Currency.enabled).order_by(Currency.sort_order)):
                item = {"currency": c.code, "name": c.name}
                for t in ("reference", "buy", "sell"):
                    r = s.scalars(select(ExchangeRate).where(
                        ExchangeRate.currency_code == c.code, ExchangeRate.rate_type == t,
                        ExchangeRate.granularity == "daily")
                        .order_by(ExchangeRate.publication_date.desc()).limit(1)).first()
                    prev = s.scalars(select(ExchangeRate).where(
                        ExchangeRate.currency_code == c.code, ExchangeRate.rate_type == t,
                        ExchangeRate.granularity == "daily", ExchangeRate.publication_date < r.publication_date)
                        .order_by(ExchangeRate.publication_date.desc()).limit(1)).first() if r else None
                    item[t] = None if not r else {
                        "value": float(r.value), "publication_date": r.publication_date.isoformat(),
                        "value_date": r.value_date.isoformat(), "retrieved_at": _iso(r.retrieved_at),
                        "change": float(r.value - prev.value) if prev else None, "source_kind": r.source_kind}
                out.append(item)
            return {"currencies": out, "demo": DEMO(),
                    "last_successful_sync": _iso(ok.finished_at) if ok else None,
                    "last_attempt": {"status": last.status, "at": _iso(last.finished_at or last.started_at)}
                    if last else None}

    # ---- history ------------------------------------------------------------------------------
    @app.get("/api/rates")
    def rates(currency: str | None = None, types: str | None = None, start: date | None = None,
              end: date | None = None, sort: str = "desc", limit: int = Query(25, le=500), offset: int = 0):
        order = ExchangeRate.publication_date.asc() if sort == "asc" else ExchangeRate.publication_date.desc()
        with sf() as s:
            base = filters(select(ExchangeRate), currency, types, start, end)
            total = s.scalar(select(func.count()).select_from(base.subquery()))
            items = s.scalars(base.order_by(order, ExchangeRate.currency_code, ExchangeRate.rate_type)
                              .limit(limit).offset(offset)).all()
            return {"total": total, "items": [row(r) for r in items]}

    @app.get("/api/series")
    def series(currency: str, types: str, start: date | None = None, end: date | None = None):
        """One list of [date, value] per rate type; a null is inserted across gaps > 7 days so the
        chart shows holes instead of connecting (or forward-filling) over missing periods."""
        out = {}
        with sf() as s:
            for t in types.split(","):
                q = filters(select(ExchangeRate.publication_date, ExchangeRate.value), currency, t, start, end)
                q = q.where(ExchangeRate.granularity == "daily").order_by(ExchangeRate.publication_date)
                pts, prev = [], None
                for d, v in s.execute(q):
                    if prev and (d - prev).days > 7:
                        pts.append([(prev + timedelta(days=1)).isoformat(), None])
                    pts.append([d.isoformat(), float(v)])
                    prev = d
                out[t] = pts
        return out

    # ---- exports ------------------------------------------------------------------------------
    @app.get("/api/annual")
    def annual():
        """Annual series (imported from the workbooks): {currency: {rate_type: [[year, value], ...]}}."""
        out = {}
        with sf() as s:
            q = select(ExchangeRate).where(ExchangeRate.granularity == "annual").order_by(ExchangeRate.publication_date)
            for r in s.scalars(q):
                out.setdefault(r.currency_code, {}).setdefault(r.rate_type, []).append([r.publication_date.year, float(r.value)])
        return out

    HEADERS = ["Currency", "Rate type", "Rate value", "Publication date", "Value date", "Retrieval timestamp (UTC)", "Source"]

    def export_rows(currency, types, start, end, sort):
        order = ExchangeRate.publication_date.asc() if sort == "asc" else ExchangeRate.publication_date.desc()
        with sf() as s:
            q = filters(select(ExchangeRate), currency, types, start, end).order_by(
                order, ExchangeRate.currency_code, ExchangeRate.rate_type)
            for r in s.scalars(q.execution_options(yield_per=1000)):
                yield [r.currency_code, r.rate_type, str(r.value), r.publication_date.isoformat(),
                       r.value_date.isoformat(), r.retrieved_at.isoformat(sep=" ", timespec="seconds"), r.source_kind]

    @app.get("/api/export.csv")
    def export_csv(currency: str | None = None, types: str | None = None, start: date | None = None,
                   end: date | None = None, sort: str = "desc"):
        def gen():
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(HEADERS)
            yield "﻿" + buf.getvalue()  # BOM so Excel reads UTF-8
            for r in export_rows(currency, types, start, end, sort):
                buf.seek(0), buf.truncate()
                w.writerow(r)
                yield buf.getvalue()
        return StreamingResponse(gen(), media_type="text/csv",
                                 headers={"Content-Disposition": 'attachment; filename="exchange_rates.csv"'})

    @app.get("/api/export.xlsx")
    def export_xlsx(currency: str | None = None, types: str | None = None, start: date | None = None,
                    end: date | None = None, sort: str = "desc"):
        from openpyxl import Workbook
        wb = Workbook(write_only=True)
        ws = wb.create_sheet("Exchange rates")
        ws.append(HEADERS)
        for r in export_rows(currency, types, start, end, sort):
            r[2] = float(r[2])
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="exchange_rates.xlsx"'})

    # ---- sync log + actions -------------------------------------------------------------------
    def run_dict(r: SyncRun, items=False):
        d = {"id": r.id, "trigger": r.trigger, "started_at": _iso(r.started_at), "finished_at": _iso(r.finished_at),
             "status": r.status, "records_found": r.records_found, "records_inserted": r.records_inserted,
             "records_updated": r.records_updated, "error_message": r.error_message}
        if items:
            d["items"] = [{"source_kind": i.source_kind, "currency": i.currency_code, "status": i.status,
                           "records_found": i.records_found, "records_inserted": i.records_inserted,
                           "records_updated": i.records_updated, "error_message": i.error_message,
                           "details": i.details} for i in r.items]
        return d

    @app.get("/api/sync-runs")
    def sync_runs(limit: int = Query(20, le=100)):
        with sf() as s:
            return [run_dict(r) for r in s.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(limit))]

    @app.get("/api/sync-runs/{run_id}")
    def sync_run(run_id: int):
        with sf() as s:
            r = s.get(SyncRun, run_id)
            if not r:
                raise HTTPException(404, "run not found")
            return run_dict(r, items=True)

    def guarded(fn, *a, **kw):
        try:
            return {"run_id": fn(sf, *a, **kw)}
        except SyncBusy as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/sync")
    def trigger_sync(simulate_fail: str = ""):
        """Manual sync. `simulate_fail=EUR` makes the demo source fail for that currency (mock mode only)."""
        fail = [c for c in simulate_fail.split(",") if c] if DEMO() else []
        return guarded(run_sync, trigger="manual", simulate_fail=fail)

    @app.post("/api/sync-runs/{run_id}/retry")
    def retry(run_id: int):
        return guarded(retry_failed, run_id)

    @app.post("/api/backfill")
    def backfill():
        return guarded(run_backfill)

    app.mount("/", StaticFiles(directory=config.BASE_DIR / "static", html=True), name="static")
    return app


def app_factory() -> FastAPI:
    return create_app()
