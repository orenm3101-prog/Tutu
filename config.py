"""
config.py — Central configuration for the TUTU scraper.

Supports two environments automatically:
  • Local development  → reads from .env file (credentials.json on disk)
  • GitHub Actions     → reads from GitHub Secrets (no file on disk needed)

The code detects which mode it's in based on which env vars are present.
"""

import os
from dotenv import load_dotenv

load_dotenv()  # Loads .env if present; has no effect on GitHub Actions

# ── Google Sheets — two credential modes ──────────────────────────────────────
#
#   LOCAL MODE:   set GOOGLE_CREDENTIALS_FILE = path to your credentials.json
#   GITHUB MODE:  set GOOGLE_CREDENTIALS_JSON = base64-encoded credentials.json
#                 (see SETUP.md for how to generate this)
#
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")  # Base64 string

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1GQBintbXyiXFEHmVAwrK467nJ8121GECe13kPUtOi-Y")
SHEET_TAB_NAME = os.getenv("SHEET_TAB_NAME", "Tel-Aviv_Apartments")

# ── Scheduler ─────────────────────────────────────────────────────────────────
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "30"))

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ── Scraper Search Parameters (Tel Aviv, Phase 1A) ────────────────────────────
YAD2_CITY_CODE = "5000"   # Yad2 internal code for Tel Aviv

MIN_PRICE = 2000
MAX_PRICE = 20000
MIN_ROOMS = 1
MAX_ROOMS = 6

# ── HTTP Settings ─────────────────────────────────────────────────────────────
REQUEST_DELAY_SECONDS   = 2.0
REQUEST_TIMEOUT_SECONDS = 15
MAX_PAGES_PER_RUN       = 5
