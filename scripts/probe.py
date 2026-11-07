"""Fetch the two Bank of Algeria pages ONCE, save the raw files, and report their structure.

Output goes to data/probe/ (raw HTML/PDF) and to the terminal. Attach data/probe/* so the parsers can be matched to the real layout.
"""
import re
from pathlib import Path

from bs4 import BeautifulSoup

from app import config
from app.sources.base import SourceError
from app.sources.http import polite_get
from app.sources.live_reference import parse_reference_html

OUT = config.DATA_DIR / "probe"
OUT.mkdir(parents=True, exist_ok=True)


def show(name, url):
    print(f"\n=== {name}: {url}")
    try:
        r = polite_get(url)
    except SourceError as e:
        print("  FAILED:", e)
        return None
    path = OUT / f"{name}.html"
    path.write_bytes(r.content)
    print(f"  HTTP {r.status_code}, {len(r.content)} bytes -> saved {path}")
    soup = BeautifulSoup(r.text, "lxml")
    tables = soup.find_all("table")
    print(f"  tables: {len(tables)}")
    for i, t in enumerate(tables[:6]):
        rows = t.find_all("tr")
        print(f"   table {i}: {len(rows)} rows; first rows:")
        for tr in rows[:3]:
            print("     ", [c.get_text(' ', strip=True)[:25] for c in tr.find_all(['th', 'td'])][:10])
    pdfs = [a["href"] for a in soup.find_all("a", href=True) if a["href"].lower().endswith(".pdf")]
    print(f"  PDF links: {len(pdfs)}", *pdfs[:10], sep="\n    ")
    sel = [s.get("name") or s.get("id") for s in soup.find_all("select")]
    print("  <select> controls (year pickers?):", sel)
    return r


r = show("reference", config.REFERENCE_URL)
if r is not None:
    try:
        recs = parse_reference_html(r.text)
        print(f"  reference parser OK: {len(recs)} records, e.g. {recs[0]}")
    except SourceError as e:
        print("  reference parser:", type(e).__name__, e)
r2 = show("home", config.QUOTATION_URL)
import pdfplumber, io
from app.sources.live_quotation import find_quotation_links
seen = []
for name, resp, base in (("reference", r, config.REFERENCE_URL), ("home", r2, config.QUOTATION_URL)):
    for url in (find_quotation_links(resp.text, base) if resp is not None else []):
        if url in seen:
            continue
        seen.append(url)
        try:
            data = polite_get(url).content
            f = OUT / url.rsplit("/", 1)[-1]
            f.write_bytes(data)
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                text = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
            print(f"\n=== PDF {url} -> {f} ({len(pdf.pages)} page(s))\n" + "\n".join(text.splitlines()[:30]))
        except Exception as e:  # noqa: BLE001
            print("  PDF failed:", url, e)
