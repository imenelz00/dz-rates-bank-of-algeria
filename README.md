# DZD Exchange Rates

A small web app that collects the USD and EUR exchange rates published by the Bank of Algeria, keeps them in a database and shows them on a simple dashboard.

Before this, you had to open the Bank of Algeria website every time you wanted a rate and there was no history. This app does the collecting automatically and keeps everything in one place.

## What it does

- Gets the **reference rates** (one value per currency per day) from the bank's daily rate page.
- Gets the **purchase and selling rates** (Achat / Vente) from the bank's daily quotation PDF.
- Imports the **annual rates** (yearly average and year-end rate, 1994 to 2025) from two Excel files the bank publishes.
- Syncs by itself when the app starts and then every hour. There is also a "Sync now" button.
- Dashboard with 3 tabs:
  - **Overview**: latest rates, daily charts, and charts of the annual rates
  - **History**: table with filters (data type, currency, rate type, dates), sorting, and export to CSV or Excel
  - **Synchronization**: status, what is stored, and a log of every sync
- Keeps a log of every sync run (how many records found / new / updated, and the error if something failed).

## Tech used

- Python 3, FastAPI (backend and API)
- SQLAlchemy with SQLite (database)
- BeautifulSoup and pdfplumber (reading the web page and the PDF)
- openpyxl (reading and writing Excel files)
- Plain HTML, CSS and JavaScript for the dashboard, with Chart.js for the charts
- pytest for the tests

The original brief said PostgreSQL. I used SQLite so the project runs without installing anything else. The code only uses normal SQLAlchemy types, so it should work on PostgreSQL by setting `DATABASE_URL`, but I did not test that.

## How to run it

I used WSL (Ubuntu) with VS Code, so the commands are for Linux. You need Python 3.10 or newer.

```bash
git clone <this repository>
cd boa-rates
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run_live.py
```

Then open http://localhost:8000

The first start imports the annual Excel files (they are in the `data/` folder) and the current rates. The first sync runs a couple of seconds after the app starts, so refresh the page after a few seconds.

`python run_live.py` is just a shortcut. It turns on the settings described in "Data access" below and uses a separate database file (`data/rates_live.db`).

### Other commands

```bash
python -m pytest -q              # run the tests
python run_live.py probe         # fetch the two bank pages once and print their structure
```

## Where the data comes from

| Data | Source | Notes |
|---|---|---|
| Reference rates (daily) | Bank of Algeria "taux de change journalier" page | The page only shows about the last 5 days, one small table per day |
| Purchase / selling (daily) | The "cotation commerciale" PDF linked on the bank's site | One PDF for the current day. It has a quotation date ("Cours du") and a value date ("Valeur") which are different |
| Annual rates | Two Excel files in `data/` | One value per year, so they are shown separately and never mixed with the daily lines |

In the app, every value is labelled **Daily · collected** or **Annual · imported**, so you can always tell where it came from.

Friday and Saturday are the weekend in Algeria and the bank publishes no rates on those days, so the charts have gaps there. I left them empty on purpose.

## How it works

1. Every source (reference page, quotation PDF) is a small class in `app/sources/`. It returns a list of records.
2. Records are validated: the value must be a number between 1 and 1000, and a jump of more than 5% from the previous day is flagged (but still saved).
3. Records are saved with a unique key (currency, rate type, publication date, value date), so running a sync twice never creates duplicates.
4. If the bank corrects a value that was already saved, the new value replaces it and the old one is kept in a `rate_revisions` table.
5. Every sync is logged in `sync_runs`, with one line per source and currency in `sync_run_items`. If one currency fails the others are still saved and the run is marked **Partial**. There is a "Retry failed" button.
6. If the site cannot be reached, nothing in the database is deleted and the error is shown in the log.
7. If the readers do not recognise the page or the PDF any more (the layout changed), they stop with an error instead of saving wrong numbers.

The date of the rate (publication date) and the time the app saved it (retrieved at) are stored separately.

## Project structure

```
app/
  main.py          API endpoints and the web server
  sync.py          daily sync and the historical import
  ingest.py        validation, saving, duplicates, corrections
  scheduler.py     automatic sync (startup and every hour)
  annual_import.py reads the two Excel files
  models.py        database tables
  sources/         the bank readers (page, PDF) and the test data generator
static/            the dashboard (HTML, CSS, JavaScript)
data/              the two Excel files
scripts/           probe.py (look at the bank pages), backfill.py
tests/             automated tests
run_live.py        starts the app in live mode
```

## Tests

There are 23 tests (`python -m pytest -q`). They cover the readers, the duplicate handling, partial and failed syncs, the catch-up after downtime, the API, the exports and the automatic start-up sync.
