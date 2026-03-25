"""
homeless.py — Scraper for Homeless (homeless.co.il).

Homeless is a rental platform popular with younger demographics in Israel.
It uses a REST/JSON API similar to Yad2.

HOW IT WORKS:
  Like Yad2, Homeless loads listings via an internal JSON API.
  We call that API directly and parse the structured response.

NOTE ON API ENDPOINT:
  If Homeless changes their API structure, open your browser,
  go to homeless.co.il, open DevTools → Network → filter by "Fetch/XHR",
  reload the page, and find the request that returns listing JSON.
  Update HOMELESS_API_URL below with the new endpoint.
"""

import logging
import re
from typing import List, Optional

from scrapers.base import BaseScraper
from models import Listing
from config import MIN_PRICE, MAX_PRICE, MIN_ROOMS, MAX_ROOMS, MAX_PAGES_PER_RUN

logger = logging.getLogger(__name__)

# Homeless internal API endpoint (reverse-engineered from browser network tab)
HOMELESS_API_URL   = "https://www.homeless.co.il/api/rent/search"
HOMELESS_BASE_URL  = "https://www.homeless.co.il"

# Homeless city name for Tel Aviv
HOMELESS_CITY = "תל אביב יפו"


class HomelessScraper(BaseScraper):
    """Scrapes rental listings from Homeless.co.il."""

    @property
    def source_name(self) -> str:
        return "Homeless"

    def fetch_listings(self) -> List[Listing]:
        """Fetches multiple pages of listings from Homeless."""
        all_listings = []

        for page in range(1, MAX_PAGES_PER_RUN + 1):
            logger.info(f"[Homeless] Fetching page {page}...")
            try:
                page_listings = self._fetch_page(page)
            except Exception as e:
                logger.error(f"[Homeless] Failed on page {page}: {e}")
                break

            if not page_listings:
                logger.info(f"[Homeless] No more results on page {page}, stopping.")
                break

            all_listings.extend(page_listings)
            logger.info(f"[Homeless] Page {page}: got {len(page_listings)} listings "
                        f"(total so far: {len(all_listings)})")

        return all_listings

    def _fetch_page(self, page: int) -> List[Listing]:
        """Fetches a single page from the Homeless API."""

        params = {
            "cityName":  HOMELESS_CITY,
            "page":      str(page),
            "pageSize":  "50",
        }
        if MIN_PRICE:
            params["priceMin"] = str(MIN_PRICE)
            params["priceMax"] = str(MAX_PRICE)
        if MIN_ROOMS:
            params["roomsMin"] = str(MIN_ROOMS)
            params["roomsMax"] = str(MAX_ROOMS)

        headers = {
            "Accept":           "application/json, text/plain, */*",
            "Accept-Language":  "he-IL,he;q=0.9",
            "Referer":          "https://www.homeless.co.il/rent",
            "Origin":           "https://www.homeless.co.il",
        }

        response = self._get(HOMELESS_API_URL, params=params, headers=headers)
        data = response.json()

        # Homeless API typically returns: { "items": [...], "total": N }
        # Adjust keys below if the structure differs — check the network tab
        items = data.get("items") or data.get("results") or data.get("data") or []

        if isinstance(items, dict):
            # Some API versions wrap items in a nested object
            items = items.get("items") or items.get("listings") or []

        listings = []
        for item in items:
            listing = self._parse_item(item)
            if listing:
                listings.append(listing)

        return listings

    def _parse_item(self, item: dict) -> Optional[Listing]:
        """Parses a single Homeless API item into a Listing object."""
        try:
            item_id = str(item.get("id") or item.get("listingId") or "")
            slug    = item.get("slug") or item.get("url") or ""
            ad_url  = f"{HOMELESS_BASE_URL}/item/{item_id}" if not slug else f"{HOMELESS_BASE_URL}/{slug.lstrip('/')}"

            # ── Address ──────────────────────────────────────────────────────
            city         = item.get("cityName") or item.get("city", "")
            neighborhood = item.get("neighborhoodName") or item.get("neighborhood", "")
            street       = item.get("streetName") or item.get("street", "")
            house_num    = str(item.get("houseNumber") or "")
            address_parts = [p for p in [street, house_num, neighborhood, city] if p]
            address = ", ".join(address_parts) or city

            if not address:
                logger.debug(f"[Homeless] Skipping item {item_id}: no address")
                return None

            # ── Price ─────────────────────────────────────────────────────────
            price_raw = item.get("price") or item.get("rentPrice")
            price     = self._parse_int(str(price_raw)) if price_raw else None

            # ── Rooms ─────────────────────────────────────────────────────────
            rooms_raw = item.get("rooms") or item.get("roomsCount")
            rooms     = self._parse_float(str(rooms_raw)) if rooms_raw else None

            # ── Size ──────────────────────────────────────────────────────────
            size_raw = item.get("size") or item.get("squareMeters") or item.get("area")
            size_sqm = self._parse_int(str(size_raw)) if size_raw else None

            # ── Floor ─────────────────────────────────────────────────────────
            floor_raw = item.get("floor") or item.get("floorNumber")
            floor     = self._parse_int(str(floor_raw)) if floor_raw else None

            # ── Phone ─────────────────────────────────────────────────────────
            contact_phone = item.get("phone") or item.get("contactPhone") or ""

            # ── Boolean features ──────────────────────────────────────────────
            # Homeless may expose these as direct boolean fields OR as free text
            def bool_field(key: str) -> Optional[bool]:
                val = item.get(key)
                if isinstance(val, bool):
                    return val
                if isinstance(val, str):
                    return val.lower() in ("true", "yes", "כן", "1")
                return None

            has_mamad    = bool_field("hasMamad")    or bool_field("mamad")
            has_balcony  = bool_field("hasBalcony")  or bool_field("balcony")
            has_rooftop  = bool_field("hasRooftop")  or bool_field("roof")
            pets_allowed = bool_field("petsAllowed") or bool_field("pets")
            is_furnished = bool_field("furnished")   or bool_field("isFurnished")
            is_renovated = bool_field("renovated")   or bool_field("isRenovated")

            # Fall back to free-text detection if boolean fields aren't present
            if any(v is None for v in [has_mamad, has_balcony, pets_allowed, is_furnished]):
                description = " ".join(filter(None, [
                    item.get("description", ""),
                    item.get("title", ""),
                    item.get("additionalInfo", ""),
                ])).lower()
                if has_mamad    is None: has_mamad    = self._detect(description, ["ממ\"ד", "ממד"])
                if has_balcony  is None: has_balcony  = self._detect(description, ["מרפסת"])
                if has_rooftop  is None: has_rooftop  = self._detect(description, ["גג"])
                if pets_allowed is None: pets_allowed = self._detect(description, ["חיות", "כלב", "חתול"])
                if is_furnished is None: is_furnished = self._detect(description, ["מרוהט"])
                if is_renovated is None: is_renovated = self._detect(description, ["משופץ"])

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
                contact_phone   = contact_phone,
            )

        except Exception as e:
            logger.warning(f"[Homeless] Error parsing item: {e}")
            return None

    # ── Helpers (same as Yad2) ────────────────────────────────────────────────

    @staticmethod
    def _parse_int(value: str) -> Optional[int]:
        digits = re.sub(r"[^\d]", "", value)
        return int(digits) if digits else None

    @staticmethod
    def _parse_float(value: str) -> Optional[float]:
        match = re.search(r"[\d]+\.?[\d]*", value)
        return float(match.group()) if match else None

    @staticmethod
    def _detect(text: str, keywords: list) -> Optional[bool]:
        for kw in keywords:
            if kw.lower() in text:
                return True
        return None
