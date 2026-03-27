"""
madlan.py — Scraper for Madlan (madlan.co.il).

HOW IT WORKS:
  Madlan is a Next.js / React app that server-side renders (SSR) the first
  50 listings directly into the HTML page inside a large inline script:

      window.__SSR_HYDRATED_CONTEXT__ = { ... }

  We fetch the page with a plain HTTP GET (no headless browser needed),
  extract that JSON blob with a regex, and parse the listing data from:

      .reduxInitialState.domainData.searchList.data.searchPoiV2.poi

  Each listing contains all the fields we need:
      id, price, beds, floor, area, address, neighbourhood,
      firstTimeSeen (ISO timestamp), generalCondition, rentalBrokerFee

  WHY THIS APPROACH?
    - No Playwright / Chromium required → much faster, no browser overhead
    - Works from any non-blocked IP (residential / self-hosted runner)
    - The SSR data is complete and structured — no DOM parsing needed
    - 50 listings per request; running hourly captures all new listings

  NOTE ON IP BLOCKING:
    Madlan's Cloudflare config blocks GitHub's cloud datacenter IPs (403).
    This scraper works correctly when run from a residential IP, e.g. via a
    GitHub Actions self-hosted runner on your own machine.
    Setup: repo → Settings → Actions → Runners → New self-hosted runner.
    Then change `runs-on: ubuntu-latest` → `runs-on: self-hosted`.
"""

import json
import logging
import re
from typing import List, Optional, Any

from curl_cffi import requests as curl_requests

from scrapers.base import BaseScraper
from models import Listing

logger = logging.getLogger(__name__)

# Tel Aviv rentals search URL
MADLAN_SEARCH_URL = (
    "https://www.madlan.co.il/for-rent/"
    "%D7%AA%D7%9C-%D7%90%D7%91%D7%99%D7%91-%D7%99%D7%A4%D7%95-%D7%99%D7%A9%D7%A8%D7%90%D7%9C"
    "?marketplace=residential"
)

# Marker regex to locate __SSR_HYDRATED_CONTEXT__ in the page HTML.
# We use string slicing (not a regex end-boundary) so that any </script>
# text embedded inside JSON string values won't cause a premature cut-off.
SSR_MARKER_RE = re.compile(r'window\.__SSR_HYDRATED_CONTEXT__\s*=\s*')

# Realistic browser headers to avoid basic bot filters
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Cache-Control": "max-age=0",
}

TEL_AVIV_CITY = "תל אביב יפו"


def _safe(d: Any, *keys, default=None):
    for k in keys:
        if d is None:
            return default
        try:
            d = d[k]
        except (KeyError, IndexError, TypeError):
            return default
    return d if d is not None else default


def _coerce_bool(val) -> Optional[bool]:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return bool(val)
    if isinstance(val, str):
        return val.lower() in ("true", "yes", "1", "כן")
    return None


def _parse_poi(poi: dict) -> Optional[Listing]:
    """
    Convert one POI entry from searchPoiV2 into a Listing.

    POI structure (from SSR data):
    {
      "id": "bB70ubdDRVN",
      "price": 4500,
      "beds": 2,
      "floor": "3",
      "area": 60,
      "address": "לבנדה 56, תל אביב יפו",
      "addressDetails": {
        "city": "תל אביב יפו",
        "streetName": "לבנדה",
        "streetNumber": "56",
        "neighbourhood": "נוה שאנן"
      },
      "firstTimeSeen": "2026-03-26T15:34:35.000Z",
      "generalCondition": "renovated",
      "rentalBrokerFee": null,
      "tags": { ... }
    }
    """
    listing_id = str(poi.get("id") or "")
    if not listing_id:
        return None

    # Only keep rental bulletins
    if poi.get("type") not in ("bulletin", None):
        return None

    addr = poi.get("addressDetails") or {}
    city         = addr.get("city") or TEL_AVIV_CITY
    neighbourhood = addr.get("neighbourhood") or ""
    street_name  = addr.get("streetName") or ""
    street_num   = addr.get("streetNumber") or ""
    street       = f"{street_name} {street_num}".strip() if street_num else street_name

    # Price
    price = poi.get("price")
    try:
        price = int(price) if price is not None else None
    except (ValueError, TypeError):
        price = None

    # Rooms (called "beds" in the API)
    rooms = poi.get("beds")
    try:
        rooms = float(rooms) if rooms is not None else None
    except (ValueError, TypeError):
        rooms = None

    # Floor
    floor = poi.get("floor")
    try:
        floor = int(floor) if floor is not None else None
    except (ValueError, TypeError):
        floor = None

    # Area in sqm
    area = poi.get("area")
    try:
        area = int(area) if area is not None else None
    except (ValueError, TypeError):
        area = None

    # Condition → renovation flag
    condition = poi.get("generalCondition") or ""
    is_renovated = _coerce_bool(condition == "renovated") if condition else None

    # Broker fee
    broker_fee = poi.get("rentalBrokerFee")
    is_broker = _coerce_bool(broker_fee) if broker_fee is not None else None

    # Publication date (ISO 8601 → DD/MM/YYYY for consistency with other scrapers)
    first_seen = poi.get("firstTimeSeen") or ""
    pub_date = ""
    if first_seen:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
            pub_date = dt.strftime("%d/%m/%Y")
        except Exception:
            pub_date = first_seen[:10]  # fallback: keep YYYY-MM-DD

    return Listing(
        ad_id          = listing_id,
        source         = "Madlan",
        ad_url         = f"https://www.madlan.co.il/item/{listing_id}",
        city           = str(city),
        neighborhood   = str(neighbourhood),
        street         = str(street),
        rooms          = rooms,
        floor          = floor,
        price_ils      = price,
        size_sqm       = area,
        has_mamad      = None,
        has_balcony    = None,
        has_rooftop    = None,
        pets_allowed   = None,
        is_furnished   = None,
        is_renovated   = is_renovated,
        available_from = "",
        is_broker      = is_broker,
        scraped_at     = "",
        publication_date = pub_date,
    )


class MadlanScraper(BaseScraper):
    """
    Scrapes rental listings from Madlan by parsing the SSR data
    embedded in the page HTML — no headless browser required.

    IMPORTANT: Requires a non-datacenter IP (residential / self-hosted runner).
    GitHub Actions cloud runners are blocked by Madlan's Cloudflare config (403).
    """

    @property
    def source_name(self) -> str:
        return "Madlan"

    def fetch_listings(self) -> List[Listing]:
        logger.info("[Madlan] Fetching page HTML to extract SSR listings...")

        try:
            resp = curl_requests.get(
                MADLAN_SEARCH_URL,
                impersonate="chrome120",
                timeout=30,
            )
        except Exception as exc:
            logger.error(f"[Madlan] HTTP request failed: {exc}")
            return []

        if resp.status_code != 200:
            logger.error(
                f"[Madlan] Got HTTP {resp.status_code}. "
                f"If this is 403, the runner IP is blocked by Cloudflare. "
                f"Use a self-hosted runner on your local machine to fix this."
            )
            return []

        html = resp.text
        logger.info(f"[Madlan] Page fetched ({len(html):,} bytes). Extracting SSR data...")

        # ── Extract __SSR_HYDRATED_CONTEXT__ ──────────────────────────────────
        # Find where the JSON value starts, then slice to the next </script>.
        # Using str.find() avoids regex stopping early on </script> inside JSON.
        marker_match = SSR_MARKER_RE.search(html)
        if not marker_match:
            logger.error("[Madlan] Could not find __SSR_HYDRATED_CONTEXT__ in page HTML.")
            logger.debug(f"[Madlan] HTML preview: {html[:500]}")
            return []

        json_start = marker_match.end()
        script_end = html.find('</script>', json_start)
        if script_end == -1:
            logger.error("[Madlan] Could not find </script> after SSR context.")
            return []

        json_text = html[json_start:script_end].strip().rstrip(';').strip()

        # Next.js SSR data often contains JavaScript-only tokens that are not
        # valid JSON.  Replace them with null before parsing.
        json_text = re.sub(r'\bundefined\b', 'null', json_text)
        json_text = re.sub(r'\bNaN\b',       'null', json_text)
        json_text = re.sub(r'-?Infinity\b',  'null', json_text)

        try:
            ctx = json.loads(json_text)
        except json.JSONDecodeError as exc:
            # Log context around the error to aid future debugging
            pos = exc.pos
            snippet = json_text[max(0, pos - 80): pos + 80]
            logger.error(f"[Madlan] Failed to parse SSR JSON: {exc}")
            logger.error(f"[Madlan] Context around error (char {pos}): {repr(snippet)}")
            return []

        # ── Navigate to the listings array ────────────────────────────────────
        poi_list = _safe(
            ctx,
            "reduxInitialState", "domainData", "searchList",
            "data", "searchPoiV2", "poi",
            default=[],
        )

        total_available = _safe(
            ctx,
            "reduxInitialState", "domainData", "searchList",
            "data", "searchPoiV2", "total",
            default=0,
        )

        logger.info(
            f"[Madlan] SSR data contains {len(poi_list)} listings "
            f"(site total: {total_available:,})"
        )

        if not poi_list:
            logger.warning("[Madlan] No listings found in SSR data.")
            return []

        # ── Parse listings ────────────────────────────────────────────────────
        listings: List[Listing] = []
        skipped_old = 0

        for poi in poi_list:
            listing = _parse_poi(poi)
            if listing is None:
                continue

            # Only keep Tel Aviv listings
            if TEL_AVIV_CITY not in listing.city:
                continue

            # Incremental scan filter
            if not listing._is_newer_than(self.since_timestamp):
                skipped_old += 1
                continue

            listings.append(listing)

        if skipped_old:
            logger.info(f"[Madlan] Skipped {skipped_old} listings older than cutoff.")

        logger.info(f"[Madlan] {len(listings)} new Tel Aviv listings extracted.")

        self._update_last_scan_time()
        return listings
