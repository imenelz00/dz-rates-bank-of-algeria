""" second source caus i noticed there are commercial opening quotation (buy / sell), published as a PDF.

Real layout (Nov 2026): a title, 'Cours du : 05 Novembre 2026', 'Valeur : 09 Novembre 2026', then one line
per currency (buy, sell).
"""
import hashlib
import io
import re
from urllib.parse import urljoin

import pdfplumber
from bs4 import BeautifulSoup

from .. import config
from .base import ParserDriftError, RateRecord, Source
from .http import polite_get
from .live_reference import parse_date, parse_decimal

DATE_ANY = r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{1,2}\s+[^\W\d_]+\s+\d{4})"  # 05/11/2026 or 05 Novembre 2026
QUOTE_DATE = re.compile(
    r"(?:cours\s+du|cotation[^0-9\n]{0,60}?\bdu|ouverture[^0-9\n]{0,60}?\bdu)\s*:?\s*" + DATE_ANY, re.I)
VALUE_DATE = re.compile(r"valeur\s*:?\s*" + DATE_ANY, re.I)
NUM = r"(\d+[.,]\d+)"


def parse_quotation_text(text: str, source_url: str = config.QUOTATION_URL,
                         wanted=("USD", "EUR")) -> list[RateRecord]:
    qd, vd = QUOTE_DATE.search(text), VALUE_DATE.search(text)
    snippet = text[:400].replace("\n", " | ")
    if not qd:
        raise ParserDriftError(f"quotation date not found. PDF text starts: {snippet!r}")
    quote_date = parse_date(qd.group(1))
    value_date = parse_date(vd.group(1)) if vd else quote_date
    raw = hashlib.sha256(text.encode()).hexdigest()[:16]
    out = []
    for code in wanted:
        m = re.search(rf"\b{code}\b[^\d\n]{{0,40}}?{NUM}\s+{NUM}", text)
        if not m:
            raise ParserDriftError(f"{code} line with purchase/selling values not found. PDF text starts: {snippet!r}")
        buy, sell = parse_decimal(m.group(1)), parse_decimal(m.group(2))
        out.append(RateRecord(code, "buy", "daily", buy, quote_date, value_date, source_url, "boa_quotation", raw))
        out.append(RateRecord(code, "sell", "daily", sell, quote_date, value_date, source_url, "boa_quotation", raw))
    return out


def parse_quotation_pdf(data: bytes, source_url: str) -> list[RateRecord]:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    if not text.strip():
        raise ParserDriftError("PDF has no extractable text")
    return parse_quotation_text(text, source_url)


LINK_RX = re.compile(r"cotation[-_]commerciale", re.I)


def find_quotation_links(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    return [urljoin(base, a["href"]) for a in soup.find_all("a", href=True) if LINK_RX.search(a["href"])]


class LiveQuotationSource(Source):
    """The commercial quotation PDF is linked from the reference-rates page """
    kind = "boa_quotation"
    rate_types = ("buy", "sell")

    def fetch(self, currency, start, end):
        links = []
        for page in (config.REFERENCE_URL, config.QUOTATION_URL):
            links = find_quotation_links(polite_get(page).text, page)
            if links:
                break
        if not links:
            raise ParserDriftError("no 'cotation-commerciale' PDF link found on the reference or home page")
        recs = parse_quotation_pdf(polite_get(links[0]).content, links[0])
        return [r for r in recs if r.currency_code == currency and start <= r.publication_date <= end]
