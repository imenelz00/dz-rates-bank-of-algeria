"""Deterministic DEMO data generator.
these are first testing data that are no longer needed but i still kept them
These are NOT real Bank of Algeria values. Levels are loosely anchored on the annual averages from the
supplied workbooks, with a seeded wobble on top, so the dashboard has realistic-looking daily series
while the real site is out of reach. Everything produced here is stored with source_kind 'mock_*'.
"""
import hashlib
import math
import random
from datetime import date, timedelta
from decimal import Decimal

from .. import config
from .base import RateRecord, Source, SourceError

ANCHORS = {  # mid-year anchors (annual averages 2019-2025 from the workbooks; 2026 invented)
    "USD": {2019: 119.36, 2020: 126.83, 2021: 135.11, 2022: 142.00, 2023: 135.85, 2024: 134.05, 2025: 131.59, 2026: 130.2},
    "EUR": {2019: 133.71, 2020: 144.88, 2021: 159.76, 2022: 149.58, 2023: 146.85, 2024: 145.03, 2025: 148.58, 2026: 150.4},
}
SPREAD = Decimal("0.0025")  # buy/sell half-spread (demo)


def is_business_day(d: date) -> bool:
    return d.weekday() in (6, 0, 1, 2, 3)  # Algerian week: Sunday-Thursday


def add_business_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if is_business_day(d):
            n -= 1
    return d


def demo_reference(currency: str, d: date) -> Decimal:
    pts = sorted(ANCHORS.get(currency, {2020: 100.0}).items())
    t = d.toordinal()
    xs = [(date(y, 7, 1).toordinal(), v) for y, v in pts]
    if t <= xs[0][0]:
        base = xs[0][1]
    elif t >= xs[-1][0]:
        base = xs[-1][1]
    else:
        base = xs[0][1]
        for (t0, v0), (t1, v1) in zip(xs, xs[1:]):
            if t0 <= t <= t1:
                base = v0 + (v1 - v0) * (t - t0) / (t1 - t0)
                break
    rng = random.Random(f"{currency}-{d.isoformat()}")
    wobble = math.sin(t / 37.0) * base * 0.006 + rng.gauss(0, base * 0.0012)
    return Decimal(str(round(base + wobble, 6)))


class _MockBase(Source):
    def __init__(self, fail_currencies=()):
        self.fail_currencies = {c.upper() for c in fail_currencies}

    def _check(self, currency):
        if currency in self.fail_currencies:
            raise SourceError(f"[demo] simulated extraction failure for {currency}")

    def earliest_date(self, currency):
        return config.MOCK_START

    def _days(self, start, end):
        d = max(start, config.MOCK_START)
        while d <= end:
            if is_business_day(d):
                yield d
            d += timedelta(days=1)


class MockReferenceSource(_MockBase):
    kind = "mock_reference"
    rate_types = ("reference",)

    def fetch(self, currency, start, end):
        self._check(currency)
        out = []
        for d in self._days(start, end):
            out.append(RateRecord(currency, "reference", "daily", demo_reference(currency, d), d, d,
                                  config.REFERENCE_URL, self.kind,
                                  hashlib.sha256(f"{currency}{d}".encode()).hexdigest()[:16]))
        return out


class MockQuotationSource(_MockBase):
    kind = "mock_quotation"
    rate_types = ("buy", "sell")

    def fetch(self, currency, start, end):
        self._check(currency)
        out = []
        for d in self._days(start, end):
            ref = demo_reference(currency, d)
            vd = add_business_days(d, 2)  # quotation date != value date, as in the real publications
            raw = hashlib.sha256(f"q{currency}{d}".encode()).hexdigest()[:16]
            for rt, factor in (("buy", 1 - SPREAD), ("sell", 1 + SPREAD)):
                out.append(RateRecord(currency, rt, "daily", (ref * factor).quantize(Decimal("0.000001")), d, vd,
                                      config.QUOTATION_URL, self.kind, raw))
        return out
