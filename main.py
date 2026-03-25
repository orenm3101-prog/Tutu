"""
main.py — TUTU Scraper Agent — Phase 1A entry point.

Two run modes:
  python main.py          → Runs continuously on a schedule (local development)
  python main.py --once   → Runs a single cycle and exits (used by GitHub Actions)
"""

import logging
import sys
import time
from datetime import datetime

import schedule

from config import SCAN_INTERVAL_MINUTES, LOG_LEVEL
from database.sheets import SheetsDB
from scrapers.yad2 import Yad2Scraper
from scrapers.madlan import MadlanScraper
from scrapers.homeless import HomelessScraper

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        # Only write log file in local mode (GitHub Actions uses stdout only)
        *([] if "--once" in sys.argv else
          [logging.FileHandler("tutu_agent1.log", encoding="utf-8")])
    ]
)
logger = logging.getLogger("main")

# ── Scraper registry ──────────────────────────────────────────────────────────
# To add a new source: instantiate it here and add it to this list.
SCRAPERS = [
    Yad2Scraper(),
    HomelessScraper(),
    MadlanScraper(),   # Stub — will return [] until implemented
]


def run_scraper_cycle():
    """
    One complete scraper cycle:
      1. Run all scrapers and collect listings
      2. Deduplicate and write new ones to Google Sheets
      3. Log a summary
    """
    cycle_start = datetime.now()
    logger.info("=" * 60)
    logger.info(f"Cycle started at {cycle_start.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    all_listings = []

    for scraper in SCRAPERS:
        logger.info(f"▶ Running: {scraper.source_name}")
        try:
            listings = scraper.fetch_listings()
            logger.info(f"  ✓ {scraper.source_name}: {len(listings)} listings fetched")
            all_listings.extend(listings)
        except Exception as e:
            # One scraper failing must never stop the others
            logger.error(f"  ✗ {scraper.source_name} FAILED: {e}", exc_info=True)

    logger.info(f"Total fetched across all sources: {len(all_listings)}")

    if all_listings:
        try:
            db = SheetsDB()
            written = db.write_new_listings(all_listings)
            logger.info(f"New rows written to Google Sheets: {written}")
        except Exception as e:
            logger.error(f"Google Sheets write FAILED: {e}", exc_info=True)
            sys.exit(1)   # Exit with error code so GitHub Actions marks the run as failed
    else:
        logger.info("No listings fetched — nothing to write.")

    elapsed = (datetime.now() - cycle_start).total_seconds()
    logger.info(f"Cycle complete in {elapsed:.1f}s.")


def main():
    logger.info("╔══════════════════════════════════════════╗")
    logger.info("║   TUTU Scraper Agent — Phase 1A          ║")
    logger.info("╚══════════════════════════════════════════╝")

    once_mode = "--once" in sys.argv

    if once_mode:
        # ── GitHub Actions mode ──────────────────────────────────────────────
        # Run exactly one cycle then exit cleanly.
        # GitHub Actions schedules the repeating runs via cron.
        logger.info("Mode: single run (--once)")
        logger.info(f"Active scrapers: {[s.source_name for s in SCRAPERS]}")
        run_scraper_cycle()
        logger.info("Single run complete. Exiting.")
    else:
        # ── Local development mode ───────────────────────────────────────────
        # Run immediately, then repeat every SCAN_INTERVAL_MINUTES.
        logger.info(f"Mode: continuous scheduler (every {SCAN_INTERVAL_MINUTES} min)")
        logger.info(f"Active scrapers: {[s.source_name for s in SCRAPERS]}")
        logger.info("Press Ctrl+C to stop.\n")

        run_scraper_cycle()
        schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(run_scraper_cycle)

        try:
            while True:
                schedule.run_pending()
                time.sleep(10)
        except KeyboardInterrupt:
            logger.info("Scraper stopped by user.")


if __name__ == "__main__":
    main()
