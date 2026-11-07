"""Import the Bank of Algeria annual workbooks (average / end-of-period rates, 1994-2025).

Layout (verified on the supplied files): a title row, then a header row starting with 'Intitulé',
'Devise', 'Base', followed by one column per year. One row per currency. i kapt only the euro and dollar ones thaught that it
might be good to keep so i added them anyway caus i couldn't find data that traces back before october the 30th
"""
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import openpyxl

from .sources.base import ParserDriftError, RateRecord

KIND = "boa_annual_xlsx"


def _num(v) -> Decimal | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, (int, float)):
        return Decimal(str(round(float(v), 6)))
    return Decimal(re.sub(r"\s", "", str(v)).replace(",", "."))


def parse_annual_workbook(path: Path, rate_type: str, wanted=("USD", "EUR")) -> list[RateRecord]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    hdr_i = next((i for i, r in enumerate(rows) if r and str(r[0] or "").strip().lower().startswith("intitul")), None)
    if hdr_i is None:
        raise ParserDriftError(f"{path.name}: header row ('Intitulé') not found")
    header = rows[hdr_i]
    years = {}
    for ci, h in enumerate(header[3:], start=3):
        try:
            years[ci] = int(str(h).strip())
        except (TypeError, ValueError):
            raise ParserDriftError(f"{path.name}: column {ci} header {h!r} is not a year")
    out = []
    for r in rows[hdr_i + 1:]:
        if not r or r[1] is None:
            continue
        code = str(r[1]).strip().upper()
        if code not in wanted:
            continue
        for ci, year in years.items():
            val = _num(r[ci]) if ci < len(r) else None
            if val is None:
                continue  # e.g. EUR before 1999
            d = date(year, 12, 31)
            out.append(RateRecord(code, rate_type, "annual", val, d, d, f"file://{path.name}", KIND, None))
    return out
