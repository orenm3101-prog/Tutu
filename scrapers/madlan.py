"""
madlan.py — Scraper for Madlan (madlan.co.il).

STATUS: Phase 1A placeholder.
Madlan uses a GraphQL API. The full implementation will be added in Phase 1A
after the Yad2 scraper is verified and running. The structure is already in
place so it can be dropped in without changing main.py.
"""

import logging
from typing import List

from scrapers.base import BaseScraper
from models import Listing

logger = logging.getLogger(__name__)

MADLAN_GRAPHQL_URL = "https://www.madlan.co.il/api/graphql"


class MadlanScraper(BaseScraper):
    """Scrapes rental listings from Madlan. (Stub — to be implemented next.)"""

    @property
    def source_name(self) -> str:
        return "Madlan"

    def fetch_listings(self) -> List[Listing]:
        """
        TODO: Implement Madlan GraphQL scraper.
        Madlan uses a GraphQL API — we will reverse-engineer the query
        from the browser's network tab and replicate it here.
        """
        logger.info("[Madlan] Scraper not yet implemented — skipping.")
        return []
