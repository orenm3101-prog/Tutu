"""
madlan.py — Scraper for Madlan (madlan.co.il).

HOW IT WORKS:
  Madlan is a React SPA that loads listings via POST requests to /api2.
  The API uses persisted GraphQL queries with custom auth — direct HTTP
  POST requests from datacenter IPs are rejected ("sorry a1").

  We use Playwright (headless Chromium) to:
    1. Navigate to the Tel Aviv rentals search page.
    2. Wait for /api2 responses via page.on("response") — this hooks at the
       Chrome DevTools Protocol level, capturing ALL network traffic including
       calls made from React's internally-cached fetch reference.
    3. Parse the response JSON to extract listing data.
    4. Scroll the page to trigger additional lazy-loaded API calls.

  WHY PLAYWRIGHT?
    - React caches the `fetch` reference at startup, so monkey-patching
      window.fetch after page load doesn't intercept their API calls.
    - XHR override likewise doesn't work (they use the Fetch API).
    - Playwright's CDP-level interception is the only reliable way to
      capture requests/responses from an SPA without modifying the app.

KNOWN RESPONSE STRUCTURE (discovered empirically — see logs):
  The /api2 endpoint returns JSON. The listings are found at:
    data → userListingsV3 → listings → [{ listing: {...} }]
  OR
    data → userListingsV2 → listings → [{ listing: {...} }]

  Each listing object has fields like:
    id, price, rooms, floor, squareMeter, address (city/street/neighbourhood),
    balconies, hasMamad, hasElevator, isFurnished, allowPets, entryDate, etc.
"""

import json
import logging
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


def _extract_listings_from_response(data: dict) -> List[dict]:
    """
    Walk the API response JSON to find the listing array.
    Tries multiple known paths since the API structure may vary.
    """
    if not isinstance(data, dict):
        return []

    logger.debug(f"[Madlan] API top-level keys: {list(data.keys())[:10]}")

    # Path 1: data → userListingsV3 → listings
    raw = _safe(data, "data", "userListingsV3", "listings")
    if isinstance(raw, list) and raw:
        return raw

    # Path 2: data → userListingsV2 → listings
    raw = _safe(data, "data", "userListingsV2", "listings")
    if isinstance(raw, list) and raw:
        return raw

    # Path 3: data → listings (flat)
    raw = _safe(data, "data", "listings")
    if isinstance(raw, list) and raw:
        return raw

    # Path 4: listings at root
    raw = data.get("listings")
    if isinstance(raw, list) and raw:
        return raw

    # Path 5: deep search for any array of dicts that look like listings
    def _find_list(obj, depth=0):
        if depth > 6 or not isinstance(obj, dict):
            return None
        for v in obj.values():
            if isinstance(v, list) and len(v) > 0:
                first = v[0]
                if isinstance(first, dict) and any(
                    k in first for k in ("price", "listing", "rooms", "id", "dealId")
                ):
                    return v
            result = _find_list(v, depth + 1)
            if result:
                return result
        return None

    found = _find_list(data)
    if found:
        logger.info(f"[Madlan] Found listing array via deep search ({len(found)} items)")
        return found

    logger.warning(
        f"[Madlan] Could not find listings array in API response. "
        f"Top-level keys: {list(data.keys())}"
    )
    return []


class MadlanScraper(BaseScraper):
    """
    Scrapes rental listings from Madlan using Playwright headless browser.

    Playwright intercepts /api2 responses at the CDP level — this works even
    though React caches the fetch() reference internally at startup.
    """

    @property
    def source_name(self) -> str:
        return "Madlan"

    def fetch_listings(self) -> List[Listing]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error(
                "[Madlan] playwright not installed. "
                "Run: pip install playwright && playwright install chromium"
            )
            return []

        captured_jsons: List[dict] = []

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

            # ── CDP-level request logging ────────────────────────────────────────
            all_requests = []  # For debugging

            def on_request(request):
                url = request.url
                if "api" in url.lower() or "madlan" in url:
                    logger.debug(f"[Madlan] Request: {request.method} {url}")

            page.on("request", on_request)

            # ── CDP-level response interception ───────────────────────────────
            def on_response(response):
                url = response.url
                status = response.status

                # Log ALL requests for debugging
                if "madlan.co.il" in url:
                    all_requests.append((url, status))

                if "/api2" in url or "/api3" in url or "/api" in url:
                    try:
                        data = response.json()
                        captured_jsons.append(data)
                        count = len(_extract_listings_from_response(data))
                        logger.info(f"[Madlan] Captured API response ({status}): {count} items from {url}")
                    except Exception as exc:
                        logger.debug(f"[Madlan] Could not parse API response: {exc}")

            page.on("response", on_response)

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

            # Wait for initial API calls to complete
            logger.info(f"[Madlan] Waiting {INITIAL_WAIT_MS}ms for initial load...")
            page.wait_for_timeout(INITIAL_WAIT_MS)

            # Check page content to verify it loaded
            try:
                page_title = page.title()
                logger.info(f"[Madlan] Page title: {page_title}")
                # Get the page URL (might have redirected)
                current_url = page.url
                logger.info(f"[Madlan] Current URL: {current_url}")
                # Check if we can find any listing elements
                listing_count = page.locator("[data-testid*='listing'], [class*='listing']").count()
                logger.info(f"[Madlan] Visible listing elements on page: {listing_count}")
            except Exception as e:
                logger.warning(f"[Madlan] Error checking page content: {e}")

            # ── Scroll to trigger lazy-loaded pages ───────────────────────────
            for i in range(MAX_PAGES_PER_RUN - 1):
                before = len(captured_jsons)
                page.evaluate("window.scrollBy(0, window.innerHeight * 3)")
                page.wait_for_timeout(SCROLL_WAIT_MS)
                new_count = len(captured_jsons) - before
                logger.info(f"[Madlan] Scroll {i+1}: {new_count} new API responses")
                if new_count == 0:
                    logger.info("[Madlan] No new responses — stopping scrolls.")
                    break

            # Log all requests made for debugging
            if not captured_jsons:
                logger.warning(f"[Madlan] No /api responses captured. All requests made:")
                for url, status in all_requests[:20]:  # Log first 20
                    logger.warning(f"  {status} {url}")

            browser.close()
            logger.info(f"[Madlan] Browser closed. Total API responses: {len(captured_jsons)}")

        # ── Parse all captured responses ──────────────────────────────────────
        seen_ids: set = set()
        listings: List[Listing] = []

        for data in captured_jsons:
            raw_list = _extract_listings_from_response(data)
            for raw in raw_list:
                try:
                    listing = _parse_listing(raw)
                except Exception as exc:
                    logger.debug(f"[Madlan] Parse error on listing: {exc}")
                    continue

                if listing is None:
                    continue
                if listing.ad_id in seen_ids:
                    continue
                seen_ids.add(listing.ad_id)

                # Keep only Tel Aviv results (the page may return nearby cities)
                if TEL_AVIV_CITY not in listing.city:
                    continue

                listings.append(listing)

        logger.info(f"[Madlan] Unique Tel Aviv listings parsed: {len(listings)}")

        # ── If parsing found nothing, dump raw sample to help debug ──────────
        if captured_jsons and not listings:
            sample = json.dumps(captured_jsons[0], ensure_ascii=False, indent=2)[:3000]
            logger.info(
                "[Madlan] Parsing found 0 listings. Raw API sample:\n" + sample
            )

        return listings
