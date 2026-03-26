"""
madlan.py — Scraper for Madlan (madlan.co.il).

HOW IT WORKS:
  Madlan is a React SPA that loads listings dynamically. Instead of trying
  to intercept API calls (which is unreliable in headless environments), we use
  DOM parsing to extract listing data directly from the rendered HTML.

  We use Playwright (headless Chromium) to:
    1. Navigate to the Tel Aviv rentals search page.
    2. Wait for page to render.
    3. Parse the DOM using CSS selectors to find and extract listing cards.
    4. Scroll the page to trigger more listings to be rendered.
    5. Repeat extraction until no new listings appear.

  WHY THIS APPROACH?
    - More reliable than API interception in restricted environments
    - Works regardless of API endpoint changes
    - Directly extracts what the user sees on screen
    - Doesn't depend on understanding internal API structure

LISTING STRUCTURE IN DOM:
  Listings are rendered as cards with class names like:
    - Price: [class containing "price"]
    - Rooms: [text containing "חד'" or "rooms count"]
    - Address/Location: [class containing "address"]
    - Other features: [various data attributes]
"""

import json
import logging
import re
from typing import List, Any, Optional

from scrapers.base import BaseScraper
from models import Listing
from config import MAX_PAGES_PER_RUN

logger = logging.getLogger(__name__)

# Tel Aviv search URL — URL-encoded Hebrew: תל-אביב-יפו-ישראל
MADLAN_SEARCH_URL = (
    "https://www.madlan.co.il/for-rent/"
    "%D7%AA%D7%9C-%D7%90%D7%91%D7%99%D7%91-%D7%99%D7%A4%D7%95-%D7%99%D7%A9%D7%A8%D7%90%D7%9C"
    "?marketplace=residential"
)

# How long to wait (ms) for listings to appear after page load / scroll
INITIAL_WAIT_MS = 6_000   # first load
SCROLL_WAIT_MS  = 3_000   # between scrolls

# Tel Aviv city name to filter results
TEL_AVIV_CITY = "תל אביב יפו"


def _safe(d: Any, *keys, default=None):
    """Safely traverse a nested dict/list: _safe(obj, 'a', 'b', 0, 'c')."""
    for k in keys:
        if d is None:
            return default
        try:
            d = d[k]
        except (KeyError, IndexError, TypeError):
            return default
    return d if d is not None else default


def _coerce_bool(val) -> Optional[bool]:
    """Convert various truthy/falsy values to bool or None."""
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return bool(val)
    if isinstance(val, str):
        return val.lower() in ("true", "yes", "1", "כן")
    try:
        return bool(val)
    except Exception:
        return None


def _parse_listing(raw: dict) -> Optional[Listing]:
    """
    Convert one raw Madlan listing dict into a Listing dataclass.

    Madlan's response wraps each listing as:
      { "token": "...", "listing": { ...actual fields... } }
    OR the listing fields may be directly in the dict.
    """
    # Unwrap the "listing" envelope if present
    item = raw.get("listing") or raw

    if not isinstance(item, dict):
        return None

    # ── Identity ──────────────────────────────────────────────────────────────
    listing_id = str(
        item.get("id") or item.get("token") or
        item.get("listingId") or item.get("dealId") or ""
    )
    if not listing_id:
        return None

    ad_url = f"https://www.madlan.co.il/item/{listing_id}"

    # ── Price ─────────────────────────────────────────────────────────────────
    price = item.get("price") or item.get("rent") or item.get("monthlyRent")
    try:
        price = int(price) if price is not None else None
    except (ValueError, TypeError):
        price = None

    # ── Rooms ─────────────────────────────────────────────────────────────────
    rooms = item.get("rooms") or item.get("roomsCount")
    try:
        rooms = float(rooms) if rooms is not None else None
    except (ValueError, TypeError):
        rooms = None

    # ── Floor ─────────────────────────────────────────────────────────────────
    floor = item.get("floor") or item.get("floorNumber")
    try:
        floor = int(floor) if floor is not None else None
    except (ValueError, TypeError):
        floor = None

    # ── Size ──────────────────────────────────────────────────────────────────
    size = (
        item.get("squareMeter") or item.get("sqm") or
        item.get("area") or item.get("size")
    )
    try:
        size = int(size) if size is not None else None
    except (ValueError, TypeError):
        size = None

    # ── Address ───────────────────────────────────────────────────────────────
    addr = item.get("address") or {}
    if isinstance(addr, str):
        city         = TEL_AVIV_CITY
        neighborhood = ""
        street       = addr
    else:
        city = (
            _safe(addr, "city", "long_name") or
            _safe(addr, "city", "text")       or
            addr.get("cityName", "")          or
            TEL_AVIV_CITY
        )
        neighborhood = (
            _safe(addr, "neighbourhood", "long_name") or
            _safe(addr, "neighborhood", "long_name")  or
            _safe(addr, "neighbourhood", "text")      or
            addr.get("neighbourhoodName", "")         or
            ""
        )
        street_name  = (
            _safe(addr, "street", "long_name") or
            _safe(addr, "street", "text")      or
            addr.get("streetName", "")         or
            ""
        )
        house_number = (
            _safe(addr, "houseNumber", "long_name") or
            addr.get("houseNumber", "") or
            ""
        )
        street = f"{street_name} {house_number}".strip() if house_number else street_name

    # ── Boolean features ──────────────────────────────────────────────────────
    has_balcony  = _coerce_bool(
        item.get("balcony") or item.get("balconies") or item.get("hasBalcony")
    )
    has_mamad    = _coerce_bool(item.get("hasMamad") or item.get("mamad"))
    has_rooftop  = _coerce_bool(
        item.get("hasRooftop") or item.get("rooftop") or item.get("penthouse")
    )
    pets_allowed = _coerce_bool(
        item.get("allowPets") or item.get("petsAllowed") or item.get("pets")
    )
    is_furnished = _coerce_bool(item.get("isFurnished") or item.get("furnished"))
    is_renovated = _coerce_bool(
        item.get("isRenovated") or item.get("renovated") or
        (item.get("condition") == "renovated")
    )

    # ── Entry date ────────────────────────────────────────────────────────────
    entry = (
        item.get("entryDate") or item.get("availableFrom") or
        item.get("enteranceDate") or ""
    )
    if entry:
        entry = str(entry)[:10]  # keep YYYY-MM-DD part

    return Listing(
        ad_id          = listing_id,
        source         = "Madlan",
        ad_url         = ad_url,
        city           = str(city or ""),
        neighborhood   = str(neighborhood or ""),
        street         = str(street or ""),
        rooms          = rooms,
        floor          = floor,
        price_ils      = price,
        size_sqm       = size,
        has_mamad      = has_mamad,
        has_balcony    = has_balcony,
        has_rooftop    = has_rooftop,
        pets_allowed   = pets_allowed,
        is_furnished   = is_furnished,
        is_renovated   = is_renovated,
        available_from = str(entry),
        is_broker      = None,
        scraped_at     = "",
    )


def _extract_listings_from_dom(soup) -> List[dict]:
    """
    Extract listing data from the rendered DOM.
    Since we can't rely on specific HTML structure, we parse the rendered HTML
    to find links that point to individual listings (/item/XXX) and extract
    nearby price/property information from the page.
    """
    listings: List[dict] = []

    try:
        # Find all links that point to listing detail pages
        listing_links = soup.find_all('a', href=re.compile(r'/item/\d+'))
        logger.debug(f"[Madlan] Found {len(listing_links)} listing links")

        seen_ids = set()

        for link in listing_links:
            try:
                # Extract listing ID from href
                href = link.get('href', '')
                listing_id_match = re.search(r'/item/(\d+)', href)
                if not listing_id_match:
                    continue

                listing_id = listing_id_match.group(1)
                if listing_id in seen_ids:
                    continue
                seen_ids.add(listing_id)

                # Get the listing card container (usually the parent or nearby element)
                card = link
                for _ in range(10):  # Walk up the tree to find the card container
                    if card.parent:
                        card = card.parent
                        # Look for text that contains property info
                        text = card.get_text()
                        if '₪' in text and ('חד' in text or 'מ"ר' in text):
                            break

                listing_text = card.get_text()

                # Create a basic listing dict
                listing = {"id": listing_id}

                # Extract price (₪ followed by numbers)
                price_match = re.search(r'₪\s*([\d,]+)', listing_text)
                if price_match:
                    listing['price'] = int(price_match.group(1).replace(',', ''))

                # Extract rooms (numbers followed by "חד" or "rooms")
                rooms_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:חד|חדרים|rooms?)', listing_text)
                if rooms_match:
                    listing['rooms'] = float(rooms_match.group(1))

                # Extract square meters (number followed by m"r or sqm)
                size_match = re.search(r'(\d+)\s*(?:מ"?ר|sqm)', listing_text)
                if size_match:
                    listing['squareMeter'] = int(size_match.group(1))

                # Extract floor
                floor_match = re.search(r'קומה\s*(?:קרקע|(\d+))', listing_text)
                if floor_match and floor_match.group(1):
                    listing['floor'] = int(floor_match.group(1))
                elif 'קרקע' in listing_text:
                    listing['floor'] = 0

                # Try to find address in nearby text
                # Address usually follows the property type and size info
                address_match = re.search(r'(?:דירה|דו-משפחתי|פנטהאוז|קוטג|בית)[^,]*,\s*([^,\n]+(?:,[^,\n]+)?)', listing_text)
                if address_match:
                    listing['address'] = address_match.group(1).strip()

                listings.append(listing)

            except Exception as e:
                logger.debug(f"[Madlan] Error parsing listing link: {e}")
                continue

        logger.debug(f"[Madlan] Extracted {len(listings)} unique listings from DOM")

    except Exception as e:
        logger.error(f"[Madlan] Error extracting listings from DOM: {e}")
        import traceback
        logger.debug(f"[Madlan] Traceback: {traceback.format_exc()}")

    return listings


class MadlanScraper(BaseScraper):
    """
    Scrapes rental listings from Madlan using Playwright + DOM parsing.

    Uses headless Chromium to load the page and parse the rendered DOM,
    extracting listing data directly from the HTML.
    """

    @property
    def source_name(self) -> str:
        return "Madlan"

    def fetch_listings(self) -> List[Listing]:
        try:
            from playwright.sync_api import sync_playwright
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error(
                "[Madlan] Required packages not installed. "
                "Run: pip install playwright beautifulsoup4 && playwright install chromium"
            )
            return []

        logger.info("[Madlan] Starting headless Chromium browser...")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="he-IL",
            )

            page = context.new_page()

            # ── Navigate to Tel Aviv rentals ──────────────────────────────────
            logger.info("[Madlan] Navigating to Tel Aviv rentals page...")
            try:
                page.goto(
                    MADLAN_SEARCH_URL,
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
            except Exception as exc:
                logger.error(f"[Madlan] Page navigation failed: {exc}")
                browser.close()
                return []

            # Wait for page to fully render
            logger.info(f"[Madlan] Waiting {INITIAL_WAIT_MS}ms for page to render...")
            page.wait_for_timeout(INITIAL_WAIT_MS)

            # Check page loaded
            try:
                page_title = page.title()
                logger.info(f"[Madlan] Page title: {page_title}")
            except Exception as e:
                logger.warning(f"[Madlan] Error reading page title: {e}")

            # ── Scroll and extract listings ───────────────────────────────────
            all_listings_raw: List[dict] = []
            seen_ids: set = set()

            for scroll_num in range(MAX_PAGES_PER_RUN):
                # Get current page HTML and parse listings
                page_html = page.content()
                soup = BeautifulSoup(page_html, "html.parser")

                # Extract listings from the page (BeautifulSoup parsing)
                new_listings = _extract_listings_from_dom(soup)
                before_count = len(all_listings_raw)

                # Add new listings (avoiding duplicates)
                for listing_raw in new_listings:
                    listing_id = listing_raw.get("id")
                    if listing_id and listing_id not in seen_ids:
                        all_listings_raw.append(listing_raw)
                        seen_ids.add(listing_id)

                new_count = len(all_listings_raw) - before_count
                logger.info(f"[Madlan] Scroll {scroll_num + 1}: extracted {new_count} new listings (total: {len(all_listings_raw)})")

                # Stop if no new listings found
                if new_count == 0 and scroll_num > 0:
                    logger.info("[Madlan] No new listings found — stopping scrolls.")
                    break

                # Scroll down to trigger lazy loading
                if scroll_num < MAX_PAGES_PER_RUN - 1:
                    page.evaluate("window.scrollBy(0, window.innerHeight * 3)")
                    page.wait_for_timeout(SCROLL_WAIT_MS)

            browser.close()
            logger.info(f"[Madlan] Browser closed. Total listings extracted: {len(all_listings_raw)}")

            # Update last scan time after successful extraction
            self._update_last_scan_time()

        # ── Parse all extracted listings ──────────────────────────────────────
        listings: List[Listing] = []

        for raw in all_listings_raw:
            try:
                listing = _parse_listing(raw)
            except Exception as exc:
                logger.debug(f"[Madlan] Parse error on listing: {exc}")
                continue

            if listing is None:
                continue

            # Keep only Tel Aviv results
            if TEL_AVIV_CITY not in listing.city:
                continue

            listings.append(listing)

        logger.info(f"[Madlan] Unique Tel Aviv listings parsed: {len(listings)}")
        return listings
