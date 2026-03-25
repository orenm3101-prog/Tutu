"""
yad2.py — Scraper for Yad2 (yad2.co.il), Israel's largest real estate portal.

HOW IT WORKS:
  Yad2's website loads listings via an internal JSON API (the same API their
  website's JavaScript calls). We call that API directly — no HTML parsing needed.
  The API returns structured JSON with full listing details.

RATE LIMITING:
  Yad2 may return 429 (Too Many Requests) or block the IP if requests are too
  frequent. The base class adds a randomized delay between every request.
  MAX_PAGES_PER_RUN in config.py limits how many pages we fetch per cycle.
"""

import logging
import re
from typing import List, Optional

from scrapers.base import BaseScraper
from models import Listing
from config import (
    YAD2_CITY_CODE, MIN_PRICE, MAX_PRICE,
    MIN_ROOMS, MAX_ROOMS, MAX_PAGES_PER_RUN
)

logger = logging.getLogger(__name__)

# Yad2's internal feed API endpoint
YAD2_API_URL = "https://gw.yad2.co.il/feed-search-legacy/realestate/rent"

# Yad2 listing detail base URL (used to build the full ad URL)
YAD2_LISTING_BASE_URL = "https://www.yad2.co.il/item/"


class Yad2Scraper(BaseScraper):
    """Scrapes rental listings from Yad2."""

    @property
    def source_name(self) -> str:
        return "Yad2"

    def fetch_listings(self) -> List[Listing]:
        """
        Fetches up to MAX_PAGES_PER_RUN pages of listings from Yad2.
        Returns a flat list of Listing objects.
        """
        all_listings = []

        for page in range(1, MAX_PAGES_PER_RUN + 1):
            logger.info(f"[Yad2] Fetching page {page}...")
            try:
                page_listings = self._fetch_page(page)
            except Exception as e:
                logger.error(f"[Yad2] Failed on page {page}: {e}")
                break

            if not page_listings:
                logger.info(f"[Yad2] No more results on page {page}, stopping.")
                break

            all_listings.extend(page_listings)
            logger.info(f"[Yad2] Page {page}: got {len(page_listings)} listings "
                        f"(total so far: {len(all_listings)})")

        return all_listings

    def _fetch_page(self, page: int) -> List[Listing]:
        """Fetches a single page from the Yad2 API and parses the results."""

        params = {
            "city":       YAD2_CITY_CODE,
            "priceOnly":  "1",
            "page":       str(page),
        }
        if MIN_PRICE:
            params["price"] = f"{MIN_PRICE}-{MAX_PRICE}"
        if MIN_ROOMS:
            params["rooms"] = f"{MIN_ROOMS}-{MAX_ROOMS}"

        # Yad2's API requires these headers to return JSON instead of redirecting
        headers = {
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8",
            "Referer":         "https://www.yad2.co.il/realestate/rent",
            "Origin":          "https://www.yad2.co.il",
        }

        response = self._get(YAD2_API_URL, params=params, headers=headers)
        data = response.json()

        # The API returns: { "data": { "feed": { "feed_items": [...] } } }
        feed_items = (
            data.get("data", {})
                .get("feed", {})
                .get("feed_items", [])
        )

        listings = []
        for item in feed_items:
            # Skip promotional / agency items that are not real listings
            if item.get("type") in ("commercial", "agency_banner", "premium_banner"):
                continue

            listing = self._parse_item(item)
            if listing:
                listings.append(listing)

        return listings

    def _parse_item(self, item: dict) -> Optional[Listing]:
        """
        Parses a single Yad2 feed item dict into a Listing object.
        Returns None if the item is missing critical fields.
        """
        try:
            item_id   = item.get("id") or item.get("token", "")
            ad_url    = f"{YAD2_LISTING_BASE_URL}{item_id}" if item_id else ""

            # ── Address ──────────────────────────────────────────────────────
            city          = item.get("city_text", "")
            neighborhood  = item.get("neighborhood_text") or item.get("area_text", "")
            street        = item.get("street", "") or item.get("street_text", "")
            house_number  = str(item.get("house_number", "")) or ""
            address_parts = [p for p in [street, house_number, neighborhood, city] if p]
            address       = ", ".join(address_parts) if address_parts else city

            if not address:
                logger.debug(f"[Yad2] Skipping item {item_id}: no address")
                return None

            # ── Price ─────────────────────────────────────────────────────────
            price_raw = item.get("price") or item.get("Price")
            price     = self._parse_int(str(price_raw)) if price_raw else None

            # ── Rooms ─────────────────────────────────────────────────────────
            rooms_raw = item.get("rooms") or item.get("Rooms")
            rooms     = self._parse_float(str(rooms_raw)) if rooms_raw else None

            # ── Size ──────────────────────────────────────────────────────────
            size_raw = item.get("square_meters") or item.get("SquareMeter")
            size_sqm = self._parse_int(str(size_raw)) if size_raw else None

            # ── Floor ─────────────────────────────────────────────────────────
            floor_raw = item.get("floor") or item.get("FloorNumber")
            floor     = self._parse_int(str(floor_raw)) if floor_raw else None

            # ── Phone ─────────────────────────────────────────────────────────
            contact_phone = item.get("contact_phone") or item.get("phone_number", "")

            # ── Boolean features — parsed from free-text tags ──────────────
            tags_text = " ".join([
                item.get("info_text", ""),
                item.get("additional_info_text", ""),
                " ".join(item.get("tags", [])),
                item.get("title_1", ""),
                item.get("title_2", ""),
            ]).lower()

            has_mamad     = self._detect(tags_text, ["ממ\"ד", "ממד", "mamad"])
            has_balcony   = self._detect(tags_text, ["מרפסת", "balcony"])
            has_rooftop   = self._detect(tags_text, ["גג", "roof", "penthouse"])
            pets_allowed  = self._detect(tags_text, ["חיות מחמד", "בע\"ח", "כלב", "חתול", "pets"])
            is_furnished  = self._detect(tags_text, ["מרוהט", "furnished"])
            is_renovated  = self._detect(tags_text, ["משופץ", "שיפוץ", "renovated"])

            return Listing(
                address        = address,
                source_platform= self.source_name,
                ad_url         = ad_url,
                price          = price,
                rooms          = rooms,
                floor          = floor,
                size_sqm       = size_sqm,
                has_mamad      = has_mamad,
                has_balcony    = has_balcony,
                has_rooftop    = has_rooftop,
                pets_allowed   = pets_allowed,
                is_furnished   = is_furnished,
                is_renovated   = is_renovated,
                contact_phone  = contact_phone,
            )

        except Exception as e:
            logger.warning(f"[Yad2] Error parsing item: {e} — item keys: {list(item.keys())}")
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_int(value: str) -> Optional[int]:
        """Extracts the first integer from a string like '4,500 ₪' → 4500."""
        digits = re.sub(r"[^\d]", "", value)
        return int(digits) if digits else None

    @staticmethod
    def _parse_float(value: str) -> Optional[float]:
        """Extracts a float from a string like '2.5 rooms' → 2.5."""
        match = re.search(r"[\d]+\.?[\d]*", value)
        return float(match.group()) if match else None

    @staticmethod
    def _detect(text: str, keywords: list) -> Optional[bool]:
        """
        Returns True if any keyword is found in text, None if not found.
        We use None (not False) when a feature isn't mentioned — the absence
        of a keyword doesn't necessarily mean the feature is absent.
        """
        for kw in keywords:
            if kw.lower() in text:
                return True
        return None
