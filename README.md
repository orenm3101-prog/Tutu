# TUTU Scraper — Phase 1A Setup Guide

## What this does
This agent scrapes rental listings from Yad2 (and later Madlan) and writes new listings into your Google Sheet every 5 minutes.

---

## Prerequisites

- Python 3.9 or higher
- A Google Cloud service account with access to your Google Sheet
- Your `credentials.json` file (see step 2 below)

---

## Step 1 — Install dependencies

Open a terminal in this folder and run:

```bash
pip install -r requirements.txt
```

---

## Step 2 — Set up Google Sheets access

You said you already have `credentials.json` ready. Place it in this folder (next to `main.py`).

> **Important:** Make sure the service account email (found inside `credentials.json` under `"client_email"`) has been granted **Editor** access to your Google Sheet.
> To do this: open the sheet → Share → paste the service account email → set to Editor.

---

## Step 3 — Configure your settings

Copy the example config file:

```bash
cp .env.example .env
```

Then open `.env` and confirm these values are correct:

```
SPREADSHEET_ID=1GQBintbXyiXFEHmVAwrK467nJ8121GECe13kPUtOi-Y
SHEET_TAB_NAME=Tel-Aviv_Apartments
SCAN_INTERVAL_MINUTES=5
```

---

## Step 4 — Add the header row to your sheet

The scraper writes data starting from row 2. Row 1 must contain the column headers **in English** (for the deduplication logic to work). Copy this exact row into row 1 of your sheet:

```
Address | Floor | Size (sqm) | Mamad | Balcony | Rooftop | Pets | Furnished | Renovated | Platform | Published | Phone | URL | ID | Price | Rooms | Available From | Status | Last Verified | Added By
```

> **Note:** The Hebrew headers you already have are fine for display — but you can add a second "internal" row if you prefer, or simply rename row 1 to the above. The scraper only uses the URL column (M) for deduplication — it doesn't read headers.

---

## Step 5 — Run the scraper

```bash
python main.py
```

You will see output like:

```
2026-03-25 10:00:00  INFO     main — Starting scraper cycle at 10:00:00
2026-03-25 10:00:02  INFO     main — Running scraper: Yad2
2026-03-25 10:00:05  INFO     main — Yad2: fetched 47 listings
2026-03-25 10:00:05  INFO     main — Total listings fetched: 47
2026-03-25 10:00:06  INFO     SheetsDB — Found 0 existing URLs in sheet.
2026-03-25 10:00:07  INFO     SheetsDB — Writing 47 new listings...
2026-03-25 10:00:09  INFO     SheetsDB — Successfully wrote 47 rows.
2026-03-25 10:00:09  INFO     main — Cycle complete in 9.2s. Next run in 5 minute(s).
```

Stop the scraper at any time with **Ctrl+C**.

A log file `tutu_agent1.log` is created in this folder for debugging.

---

## Project structure

```
tutu_scraper/
├── main.py              ← Entry point — run this
├── config.py            ← All settings (reads from .env)
├── models.py            ← Listing data structure
├── requirements.txt     ← Python dependencies
├── .env                 ← Your secrets (DO NOT commit to git)
├── credentials.json     ← Google service account key (DO NOT commit to git)
├── scrapers/
│   ├── base.py          ← Shared scraper logic (delays, headers, etc.)
│   ├── yad2.py          ← Yad2 scraper (active)
│   └── madlan.py        ← Madlan scraper (stub, coming next)
└── database/
    └── sheets.py        ← Google Sheets reader/writer
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `FileNotFoundError: credentials.json` | Make sure `credentials.json` is in the same folder as `main.py` |
| `403 Forbidden` from Google Sheets | The service account email doesn't have Editor access to the sheet — see Step 2 |
| `429 Too Many Requests` from Yad2 | Increase `REQUEST_DELAY_SECONDS` in `config.py` |
| No listings appearing | Check `tutu_agent1.log` for errors. Yad2's API may have changed — open a browser, go to yad2.co.il, open DevTools → Network → filter by `feed-search-legacy` and compare the API response structure |
| Duplicate listings | This shouldn't happen — deduplication is based on the ad URL in column M. If duplicates appear, check that column M is not empty for existing rows |
