import io
from datetime import date, timedelta
from decimal import Decimal

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import config
from app.annual_import import parse_annual_workbook
from app.db import make_session_factory
from app.ingest import upsert_records
from app.main import create_app
from app.models import ExchangeRate, RateRevision, SyncRun
from app.sources.base import ParserDriftError, RateRecord, RobotsDisallowed
from app.sources.live_quotation import parse_quotation_text
from app.sources.live_reference import parse_decimal, parse_reference_html
from app.sync import run_backfill, run_sync, retry_failed

REF_HTML = """<html><body><table>
<tr><th>Date</th><th>Dollar US</th><th>Euro</th><th>Livre Sterling</th></tr>
<tr><td>05/11/2026</td><td>129,3901</td><td>1 150,4012</td><td>170,1</td></tr>
<tr><td>04/11/2026</td><td>129,4339</td><td>150,3101</td><td>170,0</td></tr></table></body></html>"""

QUOTE_TXT = """BANQUE D'ALGERIE - COTATION D'OUVERTURE DU 05/11/2026
Date de valeur : 09/11/2026
USD   129,0666   129,7136
EUR   149,9990   150,8000
"""


def rec(cur="USD", rt="reference", d=date(2026, 1, 4), v="130.5", vd=None):
    return RateRecord(cur, rt, "daily", Decimal(v), d, vd or d, "test://", "test")


@pytest.fixture
def sf():
    return make_session_factory("sqlite:///:memory:")


# ---- parsers ---------------------------------------------------------------------------------
def test_reference_parser_reads_wide_table_and_ignores_other_currencies():
    recs = parse_reference_html(REF_HTML)
    got = {(r.currency_code, r.publication_date): r.value for r in recs}
    assert len(recs) == 4
    assert got[("EUR", date(2026, 11, 5))] == Decimal("1150.4012")  # space thousands + comma decimal
    assert {r.rate_type for r in recs} == {"reference"}


STACKED = """<div><table><tr><th></th><th>05-11-2026</th></tr><tr><td>USD</td><td>135.9382</td></tr>
<tr><td>EUR</td><td>152.7402</td></tr><tr><td>GBP</td><td>180.10</td></tr></table>
<table><tr><th>04-11-2026</th></tr><tr><td>135.9107</td></tr><tr><td>152.0705</td></tr><tr><td>179.90</td></tr></table></div>"""


def test_reference_parser_real_layout_one_table_per_date():
    recs = parse_reference_html(STACKED)
    got = {(r.currency_code, r.publication_date.isoformat()): r.value for r in recs}
    assert len(recs) == 4 and got[("USD", "2026-11-04")] == Decimal("135.9107")
    assert got[("EUR", "2026-11-05")] == Decimal("152.7402")
    with pytest.raises(ParserDriftError):  # a table with a different row count must not be guessed at
        parse_reference_html(STACKED.replace("<tr><td>179.90</td></tr>", ""))


def test_quotation_link_discovery():
    from app.sources.live_quotation import find_quotation_links
    html = '<a href="/stoodroa/2026/11/clean_cotation-commerciale-3.pdf">x</a><a href="/a/Rapport.pdf">y</a>'
    assert find_quotation_links(html, "https://www.bank-of-algeria.dz/") == [
        "https://www.bank-of-algeria.dz/stoodroa/2026/11/clean_cotation-commerciale-3.pdf"]


def test_reference_parser_fails_loudly_on_layout_change():
    with pytest.raises(ParserDriftError):
        parse_reference_html("<html><body><div>Maintenance</div></body></html>")
    with pytest.raises(ParserDriftError):
        parse_reference_html(REF_HTML.replace("129,3901", "n/a"))


def test_decimal_parsing():
    assert parse_decimal("35,0977 ") == Decimal("35.0977")


def test_quotation_parser_keeps_quotation_and_value_dates_separate():
    recs = parse_quotation_text(QUOTE_TXT)
    usd = {r.rate_type: r for r in recs if r.currency_code == "USD"}
    assert usd["buy"].value == Decimal("129.0666") and usd["sell"].value == Decimal("129.7136")
    assert usd["buy"].publication_date == date(2026, 11, 5) and usd["buy"].value_date == date(2026, 11, 9)
    with pytest.raises(ParserDriftError):
        parse_quotation_text(QUOTE_TXT.replace("EUR", "XXX"))


def test_annual_workbooks_real_files():
    avg = parse_annual_workbook(config.ANNUAL_FILES["annual_avg"], "annual_avg")
    usd = {r.publication_date.year: r.value for r in avg if r.currency_code == "USD"}
    eur = {r.publication_date.year: r.value for r in avg if r.currency_code == "EUR"}
    assert min(usd) == 1994 and max(usd) == 2025 and usd[1994] == Decimal("35.0977")
    assert min(eur) == 1999  # euro did not exist before
    assert usd[2025] == Decimal("131.587228")
    eop = parse_annual_workbook(config.ANNUAL_FILES["annual_eop"], "annual_eop")
    assert {r.currency_code for r in eop} == {"USD", "EUR"}


# ---- ingest ----------------------------------------------------------------------------------
def test_upsert_is_idempotent_and_records_revisions(sf):
    with sf() as s:
        r1 = upsert_records(s, [rec(v="130.5")]); s.commit()
        assert (r1.inserted, r1.updated) == (1, 0)
        r2 = upsert_records(s, [rec(v="130.5")]); s.commit()
        assert (r2.inserted, r2.updated, r2.unchanged) == (0, 0, 1)
        r3 = upsert_records(s, [rec(v="130.9")]); s.commit()
        assert (r3.inserted, r3.updated) == (0, 1)
        assert s.scalar(select(func.count(ExchangeRate.id))) == 1
        rev = s.scalars(select(RateRevision)).one()
        assert (rev.old_value, rev.new_value) == (Decimal("130.5"), Decimal("130.9"))


def test_validation_rejects_garbage_and_flags_jumps(sf):
    with sf() as s:
        res = upsert_records(s, [rec(d=date(2026, 1, 4), v="130"), rec(d=date(2026, 1, 5), v="-3"),
                                 rec(d=date(2026, 1, 6), v="99999"), rec(d=date(2026, 1, 7), v="150")])
        s.commit()
        assert res.rejected == 2 and res.inserted == 2 and res.flagged == 1
        assert s.scalars(select(ExchangeRate).where(ExchangeRate.flagged)).one().publication_date == date(2026, 1, 7)


def test_same_quotation_date_different_value_dates_do_not_collide(sf):
    with sf() as s:
        res = upsert_records(s, [rec(rt="buy", vd=date(2026, 1, 6)), rec(rt="buy", vd=date(2026, 1, 7))])
        assert res.inserted == 2


# ---- sync ------------------------------------------------------------------------------------
def test_partial_sync_keeps_usd_when_eur_fails_then_retry_fixes_it(sf):
    today = date(2026, 11, 5)
    run_id = run_sync(sf, trigger="manual", simulate_fail=["EUR"], today=today)
    with sf() as s:
        run = s.get(SyncRun, run_id)
        assert run.status == "partial"
        by = {(i.source_kind, i.currency_code): i.status for i in run.items}
        assert by[("mock_reference", "USD")] == "success" and by[("mock_reference", "EUR")] == "failed"
        assert s.scalar(select(func.count(ExchangeRate.id)).where(ExchangeRate.currency_code == "USD")) > 0
        assert s.scalar(select(func.count(ExchangeRate.id)).where(ExchangeRate.currency_code == "EUR")) == 0
        assert "simulated" in run.error_message
    retry_id = retry_failed(sf, run_id, today=today)
    with sf() as s:
        assert s.get(SyncRun, retry_id).status == "success"
        assert s.scalar(select(func.count(ExchangeRate.id)).where(ExchangeRate.currency_code == "EUR")) > 0


def test_total_failure_preserves_existing_data(sf):
    run_sync(sf, today=date(2026, 11, 5))
    with sf() as s:
        before = s.scalar(select(func.count(ExchangeRate.id)))
    rid = run_sync(sf, simulate_fail=["USD", "EUR"], today=date(2026, 11, 6))
    with sf() as s:
        assert s.get(SyncRun, rid).status == "failed"
        assert s.scalar(select(func.count(ExchangeRate.id))) == before


def test_sync_catches_up_after_downtime(sf):
    run_sync(sf, today=date(2026, 10, 1))
    rid = run_sync(sf, today=date(2026, 10, 15))
    with sf() as s:
        days = set(s.scalars(select(ExchangeRate.publication_date).where(
            ExchangeRate.currency_code == "USD", ExchangeRate.rate_type == "reference")))
        # every Sun-Thu between the two runs is present
        d = date(2026, 10, 1)
        while d <= date(2026, 10, 15):
            if d.weekday() in (6, 0, 1, 2, 3):
                assert d in days, d
            d += timedelta(days=1)
        assert any("catch-up" in (i.details or "") for i in s.get(SyncRun, rid).items)


def test_backfill_is_repeatable_without_duplicates(sf):
    run_backfill(sf, today=date(2026, 11, 5))
    with sf() as s:
        n = s.scalar(select(func.count(ExchangeRate.id)))
    rid = run_backfill(sf, today=date(2026, 11, 5))
    with sf() as s:
        assert s.scalar(select(func.count(ExchangeRate.id))) == n
        assert s.get(SyncRun, rid).records_inserted == 0


def test_live_mode_failure_is_logged_not_raised(sf, monkeypatch):
    def blocked(url):
        raise RobotsDisallowed("robots.txt disallows automated access")
    monkeypatch.setattr("app.sources.live_reference.polite_get", blocked)
    monkeypatch.setattr("app.sources.live_quotation.polite_get", blocked)
    rid = run_sync(sf, mode="live", today=date(2026, 11, 5))
    with sf() as s:
        run = s.get(SyncRun, rid)
        assert run.status == "failed" and "robots.txt" in run.error_message


# ---- API -------------------------------------------------------------------------------------
@pytest.fixture
def client():
    app = create_app("sqlite:///:memory:", background=False)
    run_backfill(app.state.sf, today=date(2026, 11, 5))
    return TestClient(app)


def test_api_current_and_filters(client):
    cur = client.get("/api/current").json()
    usd = cur["currencies"][0]
    assert usd["reference"]["publication_date"] == "2026-11-05"
    assert usd["buy"]["value_date"] != usd["buy"]["publication_date"]
    r = client.get("/api/rates", params={"currency": "EUR", "types": "buy,sell", "start": "2026-11-01",
                                         "end": "2026-11-05", "sort": "asc"}).json()
    assert r["total"] == 10 and r["items"][0]["publication_date"] <= r["items"][-1]["publication_date"]
    assert {i["currency"] for i in r["items"]} == {"EUR"}


def test_api_series_breaks_lines_across_gaps(client):
    s = client.get("/api/series", params={"currency": "USD", "types": "reference", "start": "2026-10-01", "end": "2026-11-05"}).json()
    assert all(p[1] is not None for p in s["reference"])  # no gap > 7 days in a normal month
    annual_free = client.get("/api/series", params={"currency": "USD", "types": "annual_avg"}).json()
    assert annual_free["annual_avg"] == []  # annual values are not part of daily charts


def test_exports(client):
    q = {"currency": "USD", "types": "reference", "start": "2026-11-01", "end": "2026-11-05"}
    csv_text = client.get("/api/export.csv", params=q).text.lstrip("﻿").splitlines()
    assert csv_text[0].startswith("Currency,Rate type,Rate value,Publication date")
    assert len(csv_text) == 1 + 5  # Sun-Thu 1..5 Nov 2026 are all business days
    x = client.get("/api/export.xlsx", params=q)
    ws = openpyxl.load_workbook(io.BytesIO(x.content)).active
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][:5] == ("Currency", "Rate type", "Rate value", "Publication date", "Value date")
    assert len(rows) == 6 and isinstance(rows[1][2], float)


# ---- permission switch -----------------------------------------------------------------------
def _fake_site(monkeypatch):
    import httpx
    from app.sources import http as h

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /")
        return httpx.Response(200, text="<html>ok</html>")
    monkeypatch.setattr(h, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(config, "REQUEST_DELAY_SECONDS", 0)
    return h


def test_robots_is_respected_by_default_and_skipped_only_with_explicit_permission(monkeypatch):
    h = _fake_site(monkeypatch)
    monkeypatch.setattr(config, "BOA_PERMISSION_GRANTED", False)
    with pytest.raises(RobotsDisallowed):
        h.polite_get("https://example.test/page")
    monkeypatch.setattr(config, "BOA_PERMISSION_GRANTED", True)
    assert h.polite_get("https://example.test/page").text == "<html>ok</html>"
