"""
yad2.py — Scraper for Yad2 (yad2.co.il), Israel's largest real estate portal.

HOW IT WORKS:
  Yad2's internal JSON API (gw.yad2.co.il) now blocks server-side requests
  with a 503 response. Instead, we fetch the rendered HTML search page and
  extract the __NEXT_DATA__ JSON block that Next.js embeds in every page.
  This block contains the first page of listings pre-loaded — no API key or
  session token required, since it's part of the public HTML response.

PAGINATION:
  Each HTML page contains ~20 private + ~20 agency listings.
  We iterate pages via the ?page=N query parameter.
  MAX_PAGES_PER_RUN in config.py limits how many pages we fetch per cycle.

LISTING URL:
  Each listing has a unique token (e.g. "wka1ncc9").
  The public URL is: https://www.yad2.co.il/item/{token}
"""

import json
import logging
import re
from typing import List, Optional

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper
from models import Listing
from config import YAD2_CITY_CODE, MAX_PAGES_PER_RUN

logger = logging.getLogger(__name__)

# Search page URL — Next.js embeds listing data directly in the HTML
YAD2_SEARCH_URL    = "https://www.yad2.co.il/realestate/rent/tel-aviv-area"
YAD2_LISTING_BASE  = "https://www.yad2.co.il/item/"

# Tag IDs for boolean features (discovered by inspecting __NEXT_DATA__)
TAG_PARKING  = 1003   # חניה
TAG_BALCONY  = 1009   # מרפסת  (may vary — we also check description)
TAG_ELEVATOR = 1010   # מעלית


class Yad2Scraper(BaseScraper):
    """Scrapes rental listings from Yad2 via HTML __NEXT_DATA__ parsing."""

    @property
    def source_name(self) -> str:
        return "Yad2"

    def fetch_listings(self) -> List[Listing]:
        all_listings = []

        for page in range(1, MAX_PAGES_PER_RUN + 1):
            logger.info(f"[Yad2] Fetching page {page}...")
            try:
                page_listings = self._fetch_page(page)
            except Exception as e:
                logger.error(f"[Yad2] Failed on page {page}: {e}")
                break

            if not page_listings:
                logger.info(f"[Yad2] No listings on page {page}, stopping.")
                break

            all_listings.extend(page_listings)
            logger.info(f"[Yad2] Page {page}: {len(page_listings)} listings "
                        f"(total: {len(all_listings)})")

        return all_listings

    def _fetch_page(self, page: int) -> List[Listing]:
        """Fetches one HTML page and extracts listings from __NEXT_DATA__."""

        params = {
            "area":   "1",
            "city":   YAD2_CITY_CODE,
            "page":   str(page),
        }

        # Mimic a real browser so the server returns full HTML with __NEXT_DATA__
        headers = {
            "Accept":           "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language":  "he-IL,he;q=0.9,en-US;q=0.8",
            "Referer":          "https://www.yad2.co.il/",
        }

        response = self._get(YAD2_SEARCH_URL, params=params, headers=headers)

        # Parse the __NEXT_DATA__ JSON block from the HTML
        soup = BeautifulSoup(response.text, "html.parser")
        next_data_tag = soup.find("script", {"id": "__NEXT_DATA__"})
        if not next_data_tag:
            logger.warning("[Yad2] __NEXT_DATA__ not found in page HTML")
            return []

        next_data = json.loads(next_data_tag.string)

        # Dig into the dehydrated React Query state to find the rent feed
        queries = (
            next_data.get("props", {})
                     .get("pageProps", {})
                     .get("dehydratedState", {})
                     .get("queries", [])
        )

        rent_feed = None
        for q in queries:
            if q.get("queryKey", [None])[0] == "realestate-rent-feed":
                rent_feed = q.get("state", {}).get("data", {})
                break

        if not rent_feed:
            logger.warning("[Yad2] Could not find realestate-rent-feed in __NEXT_DATA__")
            return []

        # Combine private-owner and agency listings, tagging broker status
        # "private" = owner posting directly (is_broker=False)
        # "agency"  = real-estate agency / broker (is_broker=True)
        private_items = [(item, False) for item in (rent_feed.get("private") or [])]
        agency_items  = [(item, True)  for item in (rent_feed.get("agency")  or [])]

        listings = []
        for item, broker in private_items + agency_items:
            listing = self._parse_item(item, is_broker=broker)
            if listing:
                listings.append(listing)

        return listings

    def _parse_item(self, item: dict, is_broker: bool = False) -> Optional[Listing]:
        """Parses a single listing dict from __NEXT_DATA__ into a Listing."""
        try:
            token  = item.get("token", "")
            ad_url = f"{YAD2_LISTING_BASE}{token}" if token else ""

            # ── Address ──────────────────────────────────────────────────────
            addr          = item.get("address", {})
            city          = addr.get("city", {}).get("text", "")
            neighborhood  = addr.get("neighborhood", {}).get("text", "")
            street        = addr.get("street", {}).get("text", "")
            house_num     = str(addr.get("house", {}).get("number", "") or "")
            floor_raw     = addr.get("house", {}).get("floor")
            floor         = int(floor_raw) if floor_raw is not None else None

            address_parts = [p for p in [street, house_num, neighborhood, city] if p]
            address       = ", ".join(address_parts) or city

            if not address:
                return None

            # ── Price / Rooms / Size ──────────────────────────────────────────
            price    = item.get("price")
            details  = item.get("additionalDetails", {})
            rooms    = details.get("roomsCount")
            size_sqm = details.get("squareMeter")
            prop_type = details.get("property", {}).get("text", "")

            # ── Features from tags ────────────────────────────────────────────
            tag_ids = {t.get("id") for t in item.get("tags", [])}
            tag_names = " ".join(t.get("name", "") for t in item.get("tags", []))

            has_parking  = TAG_PARKING  in tag_ids or None
            has_balcony  = TAG_BALCONY  in tag_ids or None
            has_rooftop  = self._detect(tag_names, ["גג", "penthouse"]) or None

            # Fallback: detect from tag names (free text)
            full_text = tag_names.lower()
            if has_balcony  is None: has_balcony  = self._detect(full_text, ["מרפסת", "balcony"])
            if has_parking  is None: has_parking  = self._detect(full_text, ["חניה", "parking"])

            pets_allowed = self._detect(full_text, ["חיות מחמד", "כלב", "חתול", "pets"])
            is_furnished = self._detect(full_text, ["מרוהט", "furnished"])
            is_renovated = self._detect(full_text, ["משופץ", "renovated"])
            has_mamad    = self._detect(full_text, ['ממ"ד', "ממד", "mamad"])

            # ── Image ─────────────────────────────────────────────────────────
            image_url = item.get("metaData", {}).get("coverImage", "")

            return Listing(
                address         = address,
                source_platform = self.source_name,
                ad_url          = ad_url,
                price           = price,
                rooms           = rooms,
                floor           = floor,
                size_sqm        = size_sqm,
                has_mamad       = has_mamad,
                has_balcony     = has_balcony,
                has_rooftop     = has_rooftop,
                pets_allowed    = pets_allowed,
                is_furnished    = is_furnished,
                is_renovated    = is_renovated,
                is_broker       = is_broker,
            )

        except Exception as e:
            logger.warning(f"[Yad2] Error parsing item: {e}")
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _detect(text: str, keywords: list) -> Optional[bool]:
        for kw in keywords:
            if kw.lower() in text.lower():
                return True
        return None
