"""Central configuration"""
import os
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# Mock project: SQLite file by default. Set DATABASE_URL=postgresql+psycopg://... to use PostgreSQL
# (the schema only uses portable SQLAlchemy types).
#now irreleveant but still kept it
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'rates.db'}")

# "mock": daily data comes from a deterministic demo generator
# "live": daily data comes from the Bank of Algeria adapters (see app/sources/live_*.py).
#runs as live by default
SOURCE_MODE = os.getenv("SOURCE_MODE", "mock")

TIMEZONE = "Africa/Algiers"
SYNC_TIME = os.getenv("SYNC_TIME", "09:30")
SCHEDULER_ENABLED = os.getenv("SCHEDULER", "1") == "1"
AUTO_BACKFILL = os.getenv("AUTO_BACKFILL", "1") == "1"  # mock convenience: import on first start

REFERENCE_URL = "https://www.bank-of-algeria.dz/taux-de-change-journalier/"
QUOTATION_URL = "https://www.bank-of-algeria.dz/"
USER_AGENT = "RatesCollector/0.1 (internal mock project)"
REQUEST_DELAY_SECONDS = 1.5  # makes it so that the requests doesn't alert/over exerts the bank's servers
BOA_CA_BUNDLE = os.getenv("BOA_CA_BUNDLE")
BOA_INSECURE_TLS = os.getenv("BOA_INSECURE_TLS", "0") == "1"
BOA_PERMISSION_GRANTED = os.getenv("BOA_PERMISSION_GRANTED", "0") == "1"

ANNUAL_FILES = {
    "annual_avg": DATA_DIR / "Cours-moyens-annuels-1994-2025.xlsx",
    "annual_eop": DATA_DIR / "Cours-fin-de-periode-annuels-1994-2025.xlsx",
}

MOCK_START = date(2020, 1, 1)

# Validation
PLAUSIBLE_RANGE = (1, 1000)   # DZD per unit of foreign currency
JUMP_THRESHOLD = 0.05         # flag day-over-day moves above 5 %
ALERT_AFTER_FAILURES = 3      # consecutive failed runs before the dashboard shows an alert

DEFAULT_CURRENCIES = [("USD", "US Dollar"), ("EUR", "Euro")]
RATE_TYPES = ("reference", "buy", "sell", "annual_avg", "annual_eop")
