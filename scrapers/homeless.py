"""
homeless.py — Scraper for Homeless (homeless.co.il).

HOW IT WORKS:
  Homeless is a classic server-rendered ASP.NET WebForms site.
  All listing data is embedded directly in the HTML — no JSON API needed.

  Flow:
    1. GET /rent/ (and /rent/2, /rent/3 …) to fetch all listing table pages.
    2. Parse listing rows from both tables in each page's HTML:
         #mainresults    — private/owner listings  (is_broker = False)
         #relatedresults — broker/agency listings  (is_broker = True)
    3. Filter rows to keep only Tel Aviv ("תל אביב") listings.
    4. For each Tel Aviv listing, GET its detail page to extract the rich
       property features (size, mamad, balcony, pets, furnished, renovated).

  Why plain GET (no POST city filter):
    The ASP.NET city filter requires POSTing ViewState + session cookies.
    Cloudflare blocks this POST from datacenter IPs (GitHub Actions).
    Plain GET requests pass through fine via curl_cffi's Chrome TLS impersonation.

TABLE COLUMN INDICES (0-based):
  Private rows (12 cells):
    2=type  3=city  4=neighborhood  5=street  6=rooms  7=floor
    8=price  9=available_from  10=last_updated  11=link

  Broker rows (11 cells — no floor column):
    2=type  3=city  4=neighborhood  5=street  6=rooms
    7=price  8=available_from  9=last_updated  10=link

DETAIL PAGE FEATURES:
  All feature icons are <div class="IconOption on|off"> elements.
  "on"  = feature IS present
  "off" = feature is NOT present
  Info items (size, floor) use class="IconOption " with no on/off marker.

  Feature names we extract:
    ריהוט        → is_furnished
    מרפסת        → has_balcony  (also has count: "מרפסת: 2")
    ממד           → has_mamad
    משופצת       → is_renovated
    חיות מחמד    → pets_allowed
    גג            → has_rooftop
    מ"ר: <n>     → size_sqm
    קומה: <n>    → floor (overrides table value if present)
    כניסה: <v>   → available_from (if not already set from table)

URL PATTERNS:
  Private listing : https://www.homeless.co.il/rent/viewad,{ID}.aspx
  Broker listing  : https://www.homeless.co.il/RentTivuch/viewad,{ID}.aspx

CITY ID:
  Tel Aviv = 203  (discovered via /WebServices/AutoComplete.asmx GetCities)
"""

import logging
import re
import time
from dataclasses import replace
from typing import List, Optional

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from scrapers.base import BaseScraper
from models import Listing
from config import MAX_PAGES_PER_RUN

logger = logging.getLogger(__name__)

HOMELESS_HOME_URL   = "https://www.homeless.co.il"
HOMELESS_RENT_URL   = "https://www.homeless.co.il/rent/"
TEL_AVIV_CITY_NAME  = "תל אביב"

# Seconds to sleep between detail-page fetches (gentle pacing)
DETAIL_FETCH_SLEEP  = 0.3


class HomelessScraper(BaseScraper):
    """Scrapes rental listings from Homeless.co.il via HTML table parsing
    plus individual detail-page enrichment for property features."""

    @property
    def source_name(self) -> str:
        return "HOMELESS"

    def fetch_listings(self) -> List[Listing]:
        """
        Phase 1 — Fetch table pages and collect basic Tel Aviv listings.
        Phase 1b — Filter by publication date (incremental scanning)
        Phase 2 — For each listing, GET its detail page to extract rich fields.
        """
        session = curl_requests.Session(impersonate="chrome110")
        session.headers.update({
            "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/110.0.0.0 Safari/537.36",
            "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8",
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

        # ── Phase 1: collect basic listing data from table pages ───────────────
        all_listings: List[Listing] = []
        old_listings_count = 0

        for page in range(1, MAX_PAGES_PER_RUN + 1):
            if page == 1:
                page_url = HOMELESS_RENT_URL
            else:
                page_url = f"{HOMELESS_HOME_URL}/rent/{page}"
                time.sleep(1.5)

            try:
                resp = session.get(
                    page_url,
                    headers={"Referer": HOMELESS_HOME_URL},
                    timeout=15,
                )
                resp.raise_for_status()
            except Exception as e:
                logger.error(f"[Homeless] Page {page} failed: {e}")
                break

            page_listings = self._parse_html(resp.text)
            ta_listings   = [l for l in page_listings if self._is_tel_aviv(l.address)]

            if not page_listings:
                logger.info(f"[Homeless] Page {page}: no listings found, stopping.")
                break

            # Filter by publication date (incremental scanning)
            new_listings = []
            for listing in ta_listings:
                if listing._is_newer_than(self.since_timestamp):
                    new_listings.append(listing)
                else:
                    old_listings_count += 1

            all_listings.extend(new_listings)
            logger.info(
                f"[Homeless] Page {page}: {len(page_listings)} total, "
                f"{len(new_listings)} new, {old_listings_count} old (running total: {len(all_listings)})"
            )

            # Stop early if we're only hitting old listings
            if new_listings == 0 and old_listings_count > 5:
                logger.info(f"[Homeless] Stopping — only old listings found.")
                self._update_last_scan_time()
                return all_listings

        # ── Phase 2: enrich each Tel Aviv listing from its detail page ─────────
        if all_listings:
            logger.info(f"[Homeless] Enriching {len(all_listings)} listings from detail pages...")
            enriched = []
            for i, listing in enumerate(all_listings):
                if i > 0:
                    time.sleep(DETAIL_FETCH_SLEEP)
                enriched.append(self._enrich_from_detail(session, listing))

            # Update last scan time after successful completion
            self._update_last_scan_time()
            return enriched

        # Update last scan time even if no new listings
        self._update_last_scan_time()
        return all_listings

    # ── Detail-page enrichment ─────────────────────────────────────────────────

    def _enrich_from_detail(self, session, listing: Listing) -> Listing:
        """
        GET the listing's detail page and extract property features
        from <div class="IconOption on|off"> elements.
        Returns a new Listing with the extra fields filled in.
        """
        try:
            resp = session.get(
                listing.ad_url,
                headers={"Referer": HOMELESS_HOME_URL},
                timeout=10,
            )
            if resp.status_code != 200:
                return listing

            soup = BeautifulSoup(resp.text, "html.parser")
            updates = {}

            for div in soup.find_all("div", class_="IconOption"):
                classes  = div.get("class", [])
                text     = div.get_text(" ", strip=True)
                is_on    = "on"  in classes
                is_off   = "off" in classes
                is_bool  = is_on or is_off   # False for plain info items

                if is_bool:
                    # ── Boolean feature icons ─────────────────────────────────
                    if "ריהוט" in text:
                        updates["is_furnished"] = is_on
                    elif "מרפסת" in text:
                        updates["has_balcony"] = is_on
                    elif "ממד" in text:
                        updates["has_mamad"] = is_on
                    elif "משופצ" in text:          # matches משופצת / משופץ
                        updates["is_renovated"] = is_on
                    elif "חיות מחמד" in text:
                        updates["pets_allowed"] = is_on
                    elif "גג" in text:
                        updates["has_rooftop"] = is_on
                else:
                    # ── Numeric / info fields ─────────────────────────────────
                    if 'מ"ר' in text:
                        m = re.search(r'(\d+)', text)
                        if m:
                            updates["size_sqm"] = int(m.group(1))
                    elif "קומה" in text:
                        # "קומה: 7 מתוך 8" → floor = 7
                        m = re.search(r'(\d+)', text)
                        if m:
                            updates["floor"] = int(m.group(1))
                    elif "כניסה" in text and not listing.available_from:
                        val = re.sub(r"כניסה\s*:\s*", "", text).strip()
                        if val and val != "מיידי":
                            updates["available_from"] = val

            return replace(listing, **updates) if updates else listing

        except Exception as e:
            logger.debug(f"[Homeless] Enrich failed for {listing.ad_url}: {e}")
            return listing

    @staticmethod
    def _is_tel_aviv(address: str) -> bool:
        """Returns True if the address belongs to Tel Aviv-Jaffa."""
        return "תל אביב" in address

    # ── HTML parsing ──────────────────────────────────────────────────────────

    def _parse_html(self, html: str) -> List[Listing]:
        """Extract listings from both private and broker tables in a Homeless page."""
        soup = BeautifulSoup(html, "html.parser")
        listings = []

        # Private/owner listings — #mainresults
        main_table = soup.find(id="mainresults")
        if main_table:
            for row in main_table.find_all("tr"):
                if not row.get("id", "").startswith("ad_"):
                    continue
                listing = self._parse_row(row, is_broker=False)
                if listing:
                    listings.append(listing)

        # Broker/agency listings — #relatedresults
        broker_table = soup.find(id="relatedresults")
        if broker_table:
            for row in broker_table.find_all("tr"):
                if not row.get("id", "").startswith("ad_"):
                    continue
                listing = self._parse_row(row, is_broker=True)
                if listing:
                    listings.append(listing)

        return listings

    def _parse_row(self, row, is_broker: bool) -> Optional[Listing]:
        """
        Parse one listing row from the table.

        Private rows (12 cells): floor at col 7, price at col 8.
        Broker rows  (11 cells): no floor,        price at col 7.
        """
        try:
            cells = row.find_all("td")
            if len(cells) < 10:
                return None

            ad_id = row["id"].replace("ad_", "")
            if is_broker:
                ad_url = f"{HOMELESS_HOME_URL}/RentTivuch/viewad,{ad_id}.aspx"
            else:
                ad_url = f"{HOMELESS_HOME_URL}/rent/viewad,{ad_id}.aspx"

            # ── Address ──────────────────────────────────────────────────────
            city         = cells[3].get_text(strip=True)
            neighborhood = cells[4].get_text(strip=True)
            street       = cells[5].get_text(strip=True)

            address_parts = [p for p in [street, neighborhood, city] if p]
            address = ", ".join(address_parts) or city
            if not address:
                return None

            # ── Rooms / Floor / Price ─────────────────────────────────────────
            rooms = self._parse_float(cells[6].get_text(strip=True))

            if is_broker:
                floor          = None
                price          = self._parse_price(cells[7].get_text(strip=True))
                available_raw  = cells[8].get_text(strip=True)
            else:
                floor          = self._parse_int(cells[7].get_text(strip=True))
                price          = self._parse_price(cells[8].get_text(strip=True))
                available_raw  = cells[9].get_text(strip=True)

            available_from = None if available_raw in ("מיידי", "") else available_raw

            return Listing(
                address         = address,
                source_platform = self.source_name,
                ad_url          = ad_url,
                price           = price,
                rooms           = rooms,
                floor           = floor,
                available_from  = available_from,
                is_broker       = is_broker,
            )

        except Exception as e:
            logger.warning(f"[Homeless] Error parsing row {row.get('id', '?')}: {e}")
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_price(text: str) -> Optional[int]:
        digits = re.sub(r"[^\d]", "", text)
        return int(digits) if digits else None

    @staticmethod
    def _parse_int(value: str) -> Optional[int]:
        digits = re.sub(r"[^\d]", "", value)
        return int(digits) if digits else None

    @staticmethod
    def _parse_float(value: str) -> Optional[float]:
        match = re.search(r"[\d]+\.?[\d]*", value)
        return float(match.group()) if match else None
