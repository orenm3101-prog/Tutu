"""
homeless.py — Scraper for Homeless (homeless.co.il).

HOW IT WORKS:
  Homeless blocks direct API requests with a 403 Forbidden response unless
  the request includes valid session cookies from a prior page visit.
  We solve this by:
    1. First GETting the homepage to obtain session cookies.
    2. Then calling the search API with those cookies attached.
  Both requests use the same requests.Session() so cookies are shared.

NOTE ON API ENDPOINT:
  If the API changes, open your browser, go to homeless.co.il,
  open DevTools → Network → filter by "Fetch/XHR", reload the page,
  and find the request that returns listing JSON. Update HOMELESS_API_URL.
"""

import logging
import re
import time
from typing import List, Optional

from curl_cffi import requests as curl_requests

from scrapers.base import BaseScraper
from models import Listing
from config import MIN_PRICE, MAX_PRICE, MIN_ROOMS, MAX_ROOMS, MAX_PAGES_PER_RUN

logger = logging.getLogger(__name__)

HOMELESS_HOME_URL  = "https://www.homeless.co.il"
HOMELESS_API_URL   = "https://www.homeless.co.il/api/rent/search"
HOMELESS_CITY      = "תל אביב יפו"


class HomelessScraper(BaseScraper):
    """Scrapes rental listings from Homeless.co.il."""

    @property
    def source_name(self) -> str:
        return "Homeless"

    def fetch_listings(self) -> List[Listing]:
        # Use curl_cffi to impersonate Chrome's TLS fingerprint (bypasses Cloudflare)
        session = curl_requests.Session(impersonate="chrome110")
        session.headers.update({
            "User-Agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/110.0.0.0 Safari/537.36",
            "Accept-Language":  "he-IL,he;q=0.9,en-US;q=0.8",
            "Accept":           "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

        try:
            logger.info("[Homeless] Warming up session via homepage...")
            session.get(HOMELESS_HOME_URL, timeout=15)
            time.sleep(2)   # polite delay after the warmup
        except Exception as e:
            logger.warning(f"[Homeless] Homepage warmup failed: {e} — continuing anyway")

        all_listings = []

        for page in range(1, MAX_PAGES_PER_RUN + 1):
            logger.info(f"[Homeless] Fetching page {page}...")
            try:
                page_listings = self._fetch_page(session, page)
            except Exception as e:
                logger.error(f"[Homeless] Failed on page {page}: {e}")
                break

            if not page_listings:
                logger.info(f"[Homeless] No listings on page {page}, stopping.")
                break

            all_listings.extend(page_listings)
            logger.info(f"[Homeless] Page {page}: {len(page_listings)} listings "
                        f"(total: {len(all_listings)})")
            time.sleep(1.5)

        return all_listings

    def _fetch_page(self, session: curl_requests.Session, page: int) -> List[Listing]:
        """Fetches a single page using the warmed-up session."""

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
            "Accept":    "application/json, text/plain, */*",
            "Referer":   "https://www.homeless.co.il/rent",
            "Origin":    "https://www.homeless.co.il",
        }

        resp = session.get(HOMELESS_API_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # API returns { "items": [...], "total": N } or similar
        items = data.get("items") or data.get("results") or data.get("data") or []
        if isinstance(items, dict):
            items = items.get("items") or items.get("listings") or []

        listings = []
        for item in items:
            listing = self._parse_item(item)
            if listing:
                listings.append(listing)

        return listings

    def _parse_item(self, item: dict) -> Optional[Listing]:
        """Parses a single Homeless API item into a Listing."""
        try:
            item_id = str(item.get("id") or item.get("listingId") or "")
            slug    = item.get("slug") or item.get("url") or ""
            ad_url  = (
                f"{HOMELESS_HOME_URL}/{slug.lstrip('/')}"
                if slug
                else f"{HOMELESS_HOME_URL}/item/{item_id}"
            )

            # ── Address ──────────────────────────────────────────────────────
            city         = item.get("cityName") or item.get("city", "")
            neighborhood = item.get("neighborhoodName") or item.get("neighborhood", "")
            street       = item.get("streetName") or item.get("street", "")
            house_num    = str(item.get("houseNumber") or "")
            address_parts = [p for p in [street, house_num, neighborhood, city] if p]
            address = ", ".join(address_parts) or city

            if not address:
                return None

            # ── Price / Rooms / Size / Floor ──────────────────────────────────
            price_raw = item.get("price") or item.get("rentPrice")
            price     = self._parse_int(str(price_raw)) if price_raw else None

            rooms_raw = item.get("rooms") or item.get("roomsCount")
            rooms     = self._parse_float(str(rooms_raw)) if rooms_raw else None

            size_raw = item.get("size") or item.get("squareMeters") or item.get("area")
            size_sqm = self._parse_int(str(size_raw)) if size_raw else None

            floor_raw = item.get("floor") or item.get("floorNumber")
            floor     = self._parse_int(str(floor_raw)) if floor_raw else None

            contact_phone = item.get("phone") or item.get("contactPhone") or ""

            # ── Boolean features ──────────────────────────────────────────────
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

            # Fallback: detect from free-text description
            description = " ".join(filter(None, [
                item.get("description", ""),
                item.get("title", ""),
                item.get("additionalInfo", ""),
            ])).lower()

            if has_mamad    is None: has_mamad    = self._detect(description, ['ממ"ד', "ממד"])
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

    # ── Helpers ───────────────────────────────────────────────────────────────

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
            if kw.lower() in text.lower():
                return True
        return None
