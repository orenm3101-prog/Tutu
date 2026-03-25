"""
base.py — Abstract base class that every scraper must implement.
This enforces a consistent interface so new scrapers (Madlan, Facebook, etc.)
can be added later without changing the main runner.
"""

import logging
import time
import random
from abc import ABC, abstractmethod
from typing import List

import requests

from models import Listing
from config import REQUEST_DELAY_SECONDS, REQUEST_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """
    All scrapers inherit from this class.
    They only need to implement `fetch_listings()`.
    """

    # Rotate through these user agents to reduce bot-detection risk
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2_1) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    ]

    def __init__(self):
        self.session = requests.Session()
        self._rotate_user_agent()

    def _rotate_user_agent(self):
        """Pick a random user agent for this session."""
        ua = random.choice(self.USER_AGENTS)
        self.session.headers.update({"User-Agent": ua})

    def _get(self, url: str, params: dict = None, headers: dict = None) -> requests.Response:
        """
        Makes a GET request with built-in delay and error handling.
        Raises requests.HTTPError on non-2xx responses.
        """
        # Polite delay: randomize slightly around the configured delay
        delay = REQUEST_DELAY_SECONDS + random.uniform(0, 1.0)
        time.sleep(delay)

        try:
            response = self.session.get(
                url,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            return response
        except requests.exceptions.Timeout:
            logger.warning(f"Request timed out: {url}")
            raise
        except requests.exceptions.HTTPError as e:
            logger.warning(f"HTTP error {e.response.status_code} for: {url}")
            raise
        except requests.exceptions.ConnectionError:
            logger.error(f"Connection failed: {url}")
            raise

    @abstractmethod
    def fetch_listings(self) -> List[Listing]:
        """
        Fetch all new listings from this source.
        Must return a list of Listing objects.
        """
        pass

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Human-readable name of this source, e.g. 'Yad2'."""
        pass
