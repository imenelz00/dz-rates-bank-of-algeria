"""Source 1 - daily reference rates from (https://www.bank-of-algeria.dz/taux-de-change-journalier/).

Layout: one small table per date, newest first.
A 'wide' table (date rows x currency columns) is kept as a fallback. If the layout differs, the parser
raises ParserDriftError.
"""
import hashlib
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

from .. import config
from .base import ParserDriftError, RateRecord, Source
from .http import polite_get

DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y")
ALIASES = {"USD": ("usd", "dollar us", "dollar am", "us dollar"), "EUR": ("eur", "euro")}
FR_MONTHS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
             "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12}


def parse_decimal(text: str) -> Decimal:
    """'1 134,5410' / '134.5410' -> Decimal. Raises ValueError on junk."""
    t = re.sub(r"[\s\u00a0]", "", text).replace(",", ".")
    try:
        return Decimal(t)
    except InvalidOperation as e:
        raise ValueError(f"not a number: {text!r}") from e


def parse_date(text: str) -> date:
    """Accepts 05-11-2026, 05/11/2026, 2026-11-05 and French long dates ('05 Novembre 2026')."""
    t = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})\s+([^\W\d_]+)\s+(\d{4})", t)
    if m:
        name = unicodedata.normalize("NFD", m.group(2).lower()).encode("ascii", "ignore").decode()
        if name in FR_MONTHS:
            return date(int(m.group(3)), FR_MONTHS[name], int(m.group(1)))
    raise ValueError(f"unrecognised date: {text!r}")


def _match_currency(header: str) -> str | None:
    h = header.strip().lower()
    for code, names in ALIASES.items():
        if any(n == h or n in h.split() or (len(n) > 3 and n in h) for n in names):
            return code
    return None


def _cells(tr) -> list[str]:
    return [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]


def _parse_stacked(soup, source_url, wanted) -> list[RateRecord] | None:
    """Layout seen on the real page: one small table per date. Row 0 ends with the date (dd-mm-yyyy);
    the first table also carries the currency codes, later tables hold only the values, same row order."""
    tables = []
    for t in soup.find_all("table"):
        rows = [r for r in (_cells(tr) for tr in t.find_all("tr")) if r]
        if len(rows) < 2:
            continue
        try:
            tables.append((parse_date(rows[0][-1]), rows))
        except ValueError:
            continue
    if not tables:
        return None
    codes = None
    for _, rows in tables:
        cand = [r[0].strip().upper() for r in rows[1:] if len(r) >= 2]
        if len(cand) == len(rows) - 1 and all(re.fullmatch(r"[A-Z]{3}", c) for c in cand):
            codes = cand
            break
    if codes is None:
        raise ParserDriftError("per-date tables found, but none lists the currency codes")
    out = []
    for d, rows in tables:
        if len(rows) - 1 != len(codes):
            raise ParserDriftError(f"table for {d} has {len(rows) - 1} rows, expected {len(codes)}")
        raw = hashlib.sha256(" | ".join(" ".join(r) for r in rows).encode()).hexdigest()[:16]
        for code, row in zip(codes, rows[1:]):
            if len(row) >= 2 and row[0].strip().upper() != code:
                raise ParserDriftError(f"currency order differs in table for {d}: {row[0]!r} vs {code}")
            if code not in wanted:
                continue
            try:
                v = parse_decimal(row[-1])
            except ValueError as e:
                raise ParserDriftError(f"reference table ({code}, {d}): {e}") from e
            out.append(RateRecord(code, "reference", "daily", v, d, d, source_url, "boa_reference", raw))
    return out


def parse_reference_html(html: str, source_url: str = config.REFERENCE_URL,
                         wanted=("USD", "EUR")) -> list[RateRecord]:
    soup = BeautifulSoup(html, "lxml")
    stacked = _parse_stacked(soup, source_url, wanted)
    if stacked:
        return stacked
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
        date_idx = next((i for i, h in enumerate(headers) if h.lower().startswith("date")), None)
        cols = {i: _match_currency(h) for i, h in enumerate(headers)}
        cols = {i: c for i, c in cols.items() if c in wanted and i != date_idx}
        if date_idx is None or not cols:
            continue
        out = []
        for tr in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) <= max(cols) or not cells[date_idx]:
                continue
            try:
                d = parse_date(cells[date_idx])
            except ValueError as e:
                raise ParserDriftError(f"reference table: {e}") from e
            raw = hashlib.sha256(" | ".join(cells).encode()).hexdigest()[:16]
            for i, code in cols.items():
                if not cells[i]:
                    continue
                try:
                    v = parse_decimal(cells[i])
                except ValueError as e:
                    raise ParserDriftError(f"reference table ({code}, {d}): {e}") from e
                out.append(RateRecord(code, "reference", "daily", v, d, d, source_url, "boa_reference", raw))
        if out:
            return out
    raise ParserDriftError("no table with a 'Date' column and USD/EUR columns found - page layout changed?")


class LiveReferenceSource(Source):
    kind = "boa_reference"
    rate_types = ("reference",)

    def fetch(self, currency, start, end):
        html = polite_get(config.REFERENCE_URL).text
        recs = parse_reference_html(html)
        return [r for r in recs if r.currency_code == currency and start <= r.publication_date <= end]