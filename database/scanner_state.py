"""
scanner_state.py — Track last scan timestamp for each source.

Enables incremental scanning: only fetch listings published AFTER the last scan.
This dramatically improves efficiency by avoiding re-downloading old listings.

Storage: JSON file in the same directory as this module.
Fallback: If file doesn't exist or is corrupted, starts fresh (full scan).
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# File to store last scan times
STATE_FILE = os.path.join(os.path.dirname(__file__), "scanner_state.json")

# Default: assume last scan was 24 hours ago (scan last day of listings)
DEFAULT_LOOKBACK_HOURS = 24


class ScannerState:
    """Manages last scan timestamp for each source."""

    def __init__(self):
        self._state = self._load_state()

    @staticmethod
    def _load_state() -> dict:
        """Load scanner state from disk, or return empty dict if file doesn't exist."""
        if not os.path.exists(STATE_FILE):
            logger.info("[ScannerState] No state file found — starting fresh (full scan on first run)")
            return {}

        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                logger.info(f"[ScannerState] Loaded scanner state from {STATE_FILE}")
                return state
        except Exception as e:
            logger.warning(f"[ScannerState] Could not load state file: {e} — starting fresh")
            return {}

    def _save_state(self):
        """Save current state to disk."""
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, ensure_ascii=False)
                logger.debug(f"[ScannerState] Saved state to {STATE_FILE}")
        except Exception as e:
            logger.error(f"[ScannerState] Failed to save state: {e}")

    def get_last_scan_time(self, source_name: str) -> Optional[datetime]:
        """
        Get the last scan timestamp for a source.
        Returns None if source has never been scanned (first run).
        """
        iso_string = self._state.get(source_name)
        if not iso_string:
            return None

        try:
            return datetime.fromisoformat(iso_string)
        except Exception as e:
            logger.warning(f"[ScannerState] Could not parse timestamp for {source_name}: {e}")
            return None

    def update_scan_time(self, source_name: str, scan_time: Optional[datetime] = None):
        """
        Update the last scan timestamp for a source.
        If scan_time is None, uses current time.
        """
        if scan_time is None:
            scan_time = datetime.now()

        self._state[source_name] = scan_time.isoformat()
        self._save_state()
        logger.info(f"[ScannerState] Updated {source_name} last scan time to {scan_time.isoformat()}")

    def get_since_timestamp(self, source_name: str) -> datetime:
        """
        Get the timestamp to use for filtering (last scan time or default lookback).
        Guaranteed to return a datetime object (never None).
        """
        last_scan = self.get_last_scan_time(source_name)

        if last_scan:
            logger.info(f"[ScannerState] {source_name}: scanning since {last_scan.isoformat()}")
            return last_scan
        else:
            # First run: look back DEFAULT_LOOKBACK_HOURS
            since = datetime.now() - timedelta(hours=DEFAULT_LOOKBACK_HOURS)
            logger.info(
                f"[ScannerState] {source_name}: first scan, looking back {DEFAULT_LOOKBACK_HOURS} hours "
                f"(since {since.isoformat()})"
            )
            return since

    def reset_source(self, source_name: str):
        """Reset a source's scan time (useful for debugging/testing)."""
        if source_name in self._state:
            del self._state[source_name]
            self._save_state()
            logger.info(f"[ScannerState] Reset scan time for {source_name}")

    def reset_all(self):
        """Reset all scan times (full rescan on next cycle)."""
        self._state = {}
        self._save_state()
        logger.warning("[ScannerState] Reset all scan times — next cycle will do full scan")
