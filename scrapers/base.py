"""
base.py — Abstract base class that every scraper must implement.

WHY curl_cffi?
  Yad2 and Homeless are both protected by Cloudflare, which blocks plain
  HTTP requests from datacenter IPs (e.g. GitHub Actions). The standard
  `requests` library is detected and served a bot-challenge page.

  curl_cffi is a Python binding for libcurl that impersonates Chrome's
  exact TLS fingerprint (JA3/ALPN/HTTP2 settings). This makes our requests
  indistinguishable from a real Chrome browser at the network level, which
  is enough to bypass Cloudflare's JS-challenge and bot-score checks.
"""

import logging
import time
import random
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional

from curl_cffi import requests as curl_requests

from models import Listing
from config import REQUEST_DELAY_SECONDS, REQUEST_TIMEOUT_SECONDS
from database.scanner_state import ScannerState

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """All scrapers inherit from this class."""

    def __init__(self):
        # impersonate="chrome110" uses Chrome 110's exact TLS fingerprint
        self.session = curl_requests.Session(impersonate="chrome110")
        self.session.headers.update({
            "User-Agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/110.0.0.0 Safari/537.36",
            "Accept-Language":  "he-IL,he;q=0.9,en-US;q=0.8",
        })

        # Initialize scanner state for incremental scanning
        self.scanner_state = ScannerState()
        self.since_timestamp = self.scanner_state.get_since_timestamp(self.source_name)

    def _get(self, url: str, params: dict = None, headers: dict = None):
        """
        Makes a GET request with polite delay and error handling.
        Uses curl_cffi session with Chrome TLS fingerprint.
        """
        delay = REQUEST_DELAY_SECONDS + random.uniform(0, 1.0)
        time.sleep(delay)

        try:
            response = self.session.get(
                url,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response
        except Exception as e:
            logger.warning(f"HTTP error for {url}: {e}")
            raise

    @abstractmethod
    def fetch_listings(self) -> List[Listing]:
        """Fetch all new listings from this source. Returns list of Listing objects."""
        pass

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Human-readable name of this source, e.g. 'YAD2'."""
        pass

    def _update_last_scan_time(self, scan_time: Optional[datetime] = None):
        """
        Update the last scan timestamp for this source.
        Call this at the END of fetch_listings() to record that the scan completed.
        """
        self.scanner_state.update_scan_time(self.source_name, scan_time)
