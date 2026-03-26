"""
homeless.py — Scraper for Homeless (homeless.co.il).

HOW IT WORKS:
  Homeless is a classic server-rendered ASP.NET WebForms site.
  All listing data is embedded directly in the HTML — no JSON API needed.

  Flow:
    1. GET /rent/ to obtain session cookies + ASP.NET ViewState fields.
    2. POST /rent/ with Tel Aviv city ID (203) to apply the city filter.
    3. Parse listing rows from both tables in the returned HTML:
         #mainresults   — private/owner listings   (is_broker = False)
         #relatedresults — broker/agency listings  (is_broker = True)
    4. Paginate via GET /rent/2, /rent/3 … The session cookie (set in step 2)
       carries the city filter forward to all subsequent GET requests.

TABLE COLUMN INDICES (0-based):
  Private rows (12 cells):
    2=type  3=city  4=neighborhood  5=street  6=rooms  7=floor
    8=price  9=available_from  10=last_updated  11=link

  Broker rows (11 cells — no floor column):
    2=type  3=city  4=neighborhood  5=street  6=rooms
    7=price  8=available_from  9=last_updated  10=link

URL PATTERNS:
  Private listing : https://www.homeless.co.il/rent/viewad,{ID}.aspx
  Broker listing  : https://www.homeless.co.il/RentTivuch/viewad,{ID}.aspx

CITY ID:
  Tel Aviv = 203  (discovered via /WebServices/AutoComplete.asmx GetCities)
"""

import logging
import re
import time
from typing import List, Optional

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from scrapers.base import BaseScraper
from models import Listing
from config import MIN_PRICE, MAX_PRICE, MIN_ROOMS, MAX_ROOMS, MAX_PAGES_PER_RUN

logger = logging.getLogger(__name__)

HOMELESS_HOME_URL   = "https://www.homeless.co.il"
HOMELESS_RENT_URL   = "https://www.homeless.co.il/rent/"
TEL_AVIV_CITY_ID    = "203"
TEL_AVIV_CITY_NAME  = "תל אביב"


class HomelessScraper(BaseScraper):
    """Scrapes rental listings from Homeless.co.il via HTML table parsing."""

    @property
    def source_name(self) -> str:
        return "HOMELESS"

    def fetch_listings(self) -> List[Listing]:
        session = curl_requests.Session(impersonate="chrome110")
        session.headers.update({
            "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/110.0.0.0 Safari/537.36",
            "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8",
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

        # ── Step 1: GET the page to collect session cookie + ViewState ────────
        try:
            logger.info("[Homeless] GET /rent/ for cookies + ViewState...")
            get_resp = session.get(HOMELESS_RENT_URL, timeout=15)
            get_resp.raise_for_status()
        except Exception as e:
            logger.error(f"[Homeless] Initial GET failed: {e}")
            return []

        soup_init = BeautifulSoup(get_resp.text, "html.parser")

        def _hidden(name: str) -> str:
            el = soup_init.find("input", {"name": name})
            return el["value"] if el and el.get("value") else ""

        viewstate        = _hidden("__VIEWSTATE")
        viewstate_gen    = _hidden("__VIEWSTATEGENERATOR")
        event_validation = _hidden("__EVENTVALIDATION")

        if not viewstate:
            logger.warning("[Homeless] ViewState not found in page — continuing without it")

        time.sleep(1.5)

        # ── Step 2: POST with Tel Aviv city filter ────────────────────────────
        post_data = {
            "__VIEWSTATE":                        viewstate,
            "__VIEWSTATEGENERATOR":               viewstate_gen,
            "__EVENTVALIDATION":                  event_validation,
            "ctl00$hdnBoardType":                 "rent",
            "ctl00$hdnShouldShowWelcomePopup":    "0",
            "iNumber1":                           TEL_AVIV_CITY_ID,
            "city":                               TEL_AVIV_CITY_NAME,
            "iNumber3":                           "",
            "iNumber4":                           str(int(MIN_ROOMS)) if MIN_ROOMS else "1",
            "iNumber4_1":                         str(int(MAX_ROOMS)) if MAX_ROOMS else "16",
            "fLong3":                             str(MIN_PRICE) if MIN_PRICE else "1000",
            "fLong3_1":                           str(MAX_PRICE) if MAX_PRICE else "1000000",
            "iNumber12":                          "-2",
            "iNumber12_1":                        "51",
            "SearchFor":                          "",
            "boardType":                          "rent",
            "view":                               "",
        }

        try:
            logger.info("[Homeless] POST city filter (Tel Aviv)...")
            post_resp = session.post(
                HOMELESS_RENT_URL,
                data=post_data,
                headers={"Referer": HOMELESS_RENT_URL},
                timeout=15,
            )
            post_resp.raise_for_status()
        except Exception as e:
            logger.error(f"[Homeless] POST failed: {e}")
            return []

        all_listings = []

        # Parse page 1 from POST response
        page1 = self._parse_html(post_resp.text)
        all_listings.extend(page1)
        logger.info(f"[Homeless] Page 1: {len(page1)} listings (total: {len(all_listings)})")

        if not page1:
            logger.warning("[Homeless] Page 1 returned 0 listings — city filter may have failed")
            return all_listings

        # ── Pages 2+ via GET (session cookie keeps the city filter) ──────────
        for page in range(2, MAX_PAGES_PER_RUN + 1):
            time.sleep(1.5)
            page_url = f"{HOMELESS_HOME_URL}/rent/{page}"
            try:
                resp = session.get(
                    page_url,
                    headers={"Referer": HOMELESS_RENT_URL},
                    timeout=15,
                )
                resp.raise_for_status()
            except Exception as e:
                logger.error(f"[Homeless] Page {page} GET failed: {e}")
                break

            page_listings = self._parse_html(resp.text)
            if not page_listings:
                logger.info(f"[Homeless] Page {page}: no listings, stopping.")
                break

            all_listings.extend(page_listings)
            logger.info(f"[Homeless] Page {page}: {len(page_listings)} listings "
                        f"(total: {len(all_listings)})")

        return all_listings

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
        Parse one listing row.

        Private rows (12 cells): floor at col 7, price at col 8.
        Broker rows  (11 cells): no floor,       price at col 7.
        Distinction is made by is_broker flag (tables are already separate).
        """
        try:
            cells = row.find_all("td")
            # Need at least city + rooms + price columns
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
                # 11-cell row: price at index 7, no floor
                floor = None
                price = self._parse_price(cells[7].get_text(strip=True))
                available_raw = cells[8].get_text(strip=True)
            else:
                # 12-cell row: floor at 7, price at 8
                floor = self._parse_int(cells[7].get_text(strip=True))
                price = self._parse_price(cells[8].get_text(strip=True))
                available_raw = cells[9].get_text(strip=True)

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
