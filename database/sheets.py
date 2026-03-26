"""
sheets.py — Google Sheets database interface.

Supports two credential modes automatically:
  • Local:   reads credentials.json from disk (GOOGLE_CREDENTIALS_FILE)
  • GitHub:  reads base64-encoded JSON from env var (GOOGLE_CREDENTIALS_JSON)

No code change is needed when switching between environments.
"""

import base64
import json
import logging
import tempfile
import os
from typing import List, Set

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from models import Listing
from config import (
    GOOGLE_CREDENTIALS_FILE,
    GOOGLE_CREDENTIALS_JSON,
    SPREADSHEET_ID,
    SHEET_TAB_NAME,
)

logger = logging.getLogger(__name__)

URL_COLUMN_INDEX = 12   # Column M (0-based) — used for deduplication
ID_COLUMN_INDEX  = 13   # Column N (0-based) — unique ID
SCOPES           = ["https://www.googleapis.com/auth/spreadsheets"]
HEADER_ROW       = 1    # Row 1 = headers; data starts at row 2


class SheetsDB:
    """
    Thin wrapper around the Google Sheets API for the TUTU listings database.
    Automatically detects whether to use a credentials file or a base64 secret.
    """

    def __init__(self):
        self._service = self._build_service()
        logger.info(f"[SheetsDB] Connected to spreadsheet: {SPREADSHEET_ID}")

    # ── Authentication — handles both local and GitHub environments ───────────

    def _build_service(self):
        """
        Builds the Google Sheets API client.

        Priority:
          1. GOOGLE_CREDENTIALS_JSON env var (base64) — used by GitHub Actions
          2. GOOGLE_CREDENTIALS_FILE path — used locally
        """
        if GOOGLE_CREDENTIALS_JSON:
            # GitHub mode: decode the base64 secret to a temporary file
            logger.info("[SheetsDB] Using credentials from GOOGLE_CREDENTIALS_JSON secret.")
            creds = self._creds_from_base64(GOOGLE_CREDENTIALS_JSON)
        elif os.path.exists(GOOGLE_CREDENTIALS_FILE):
            # Local mode: read credentials.json from disk
            logger.info(f"[SheetsDB] Using credentials file: {GOOGLE_CREDENTIALS_FILE}")
            creds = service_account.Credentials.from_service_account_file(
                GOOGLE_CREDENTIALS_FILE, scopes=SCOPES
            )
        else:
            raise FileNotFoundError(
                "No Google credentials found.\n"
                "  • Local: place credentials.json in this folder, or set GOOGLE_CREDENTIALS_FILE in .env\n"
                "  • GitHub: add GOOGLE_CREDENTIALS_JSON as a repository secret (see SETUP.md)"
            )

        return build("sheets", "v4", credentials=creds, cache_discovery=False)

    @staticmethod
    def _creds_from_base64(b64_string: str) -> service_account.Credentials:
        """Decodes a base64-encoded credentials JSON string into a Credentials object."""
        try:
            json_bytes = base64.b64decode(b64_string)
            info = json.loads(json_bytes)
            return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        except Exception as e:
            raise ValueError(
                f"Failed to decode GOOGLE_CREDENTIALS_JSON secret: {e}\n"
                "Make sure it was base64-encoded correctly (see SETUP.md Step 3)."
            )

    # ── Public API ────────────────────────────────────────────────────────────

    def write_new_listings(self, listings: List[Listing]) -> int:
        """
        Deduplicates incoming listings against existing URLs in the sheet,
        then appends only the new ones.
        Returns the number of rows actually written.
        """
        if not listings:
            return 0

        existing_urls = self._get_existing_urls()
        logger.info(f"[SheetsDB] {len(existing_urls)} existing URLs found (deduplication set).")

        new_listings = [l for l in listings if l.ad_url not in existing_urls]

        if not new_listings:
            logger.info("[SheetsDB] All listings already in sheet — nothing to write.")
            return 0

        logger.info(f"[SheetsDB] {len(new_listings)} new listings to write "
                    f"({len(listings) - len(new_listings)} duplicates skipped).")

        next_id = self._get_next_id(len(existing_urls))
        rows = []
        for i, listing in enumerate(new_listings):
            row = listing.to_sheet_row()
            row[ID_COLUMN_INDEX] = next_id + i
            rows.append(row)

        self._append_rows(rows)
        logger.info(f"[SheetsDB] Successfully wrote {len(rows)} rows.")
        return len(rows)

    def get_all_active_listings(self) -> List[dict]:
        """
        Returns all rows where Status = 'Active'.
        Used by Agent 2 (Matcher) and Agent 3 (Staleness Checker).
        """
        all_rows = self._read_all_rows()
        active = [r for r in all_rows if r.get("status", "").lower() == "active"]
        logger.info(f"[SheetsDB] {len(active)} active listings found.")
        return active

    def update_listing_status(self, row_number: int, status: str, last_verified: str):
        """
        Updates Status (col R) and Last Verified (col S) for a specific row.
        row_number is 1-based. Used by Agent 3.
        """
        for col, value in [("R", status), ("S", last_verified)]:
            range_ref = f"{SHEET_TAB_NAME}!{col}{row_number}"
            try:
                self._service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=range_ref,
                    valueInputOption="RAW",
                    body={"values": [[value]]}
                ).execute()
            except HttpError as e:
                logger.error(f"[SheetsDB] Failed to update {col}{row_number}: {e}")

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_existing_urls(self) -> Set[str]:
        """Reads column M only (fast) to build the deduplication set."""
        range_name = f"{SHEET_TAB_NAME}!M2:M"
        try:
            result = self._service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=range_name
            ).execute()
            rows = result.get("values", [])
            return {row[0].strip() for row in rows if row and row[0].strip()}
        except HttpError as e:
            logger.error(f"[SheetsDB] Could not read existing URLs: {e}")
            return set()

    def _get_next_id(self, current_row_count: int) -> int:
        return current_row_count + 1

    def _append_rows(self, rows: List[list]):
        """Appends rows to the bottom of the sheet."""
        body = {"values": rows}
        try:
            self._service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{SHEET_TAB_NAME}!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body=body
            ).execute()
        except HttpError as e:
            logger.error(f"[SheetsDB] Failed to append rows: {e}")
            raise

    def _read_all_rows(self) -> List[dict]:
        """Reads the full sheet and returns rows as dicts keyed by field name."""
        range_name = f"{SHEET_TAB_NAME}!A1:U"
        try:
            result = self._service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=range_name
            ).execute()
            rows = result.get("values", [])
        except HttpError as e:
            logger.error(f"[SheetsDB] Could not read sheet: {e}")
            return []

        if len(rows) < 2:
            return []

        keys = [
            "address", "floor", "size_sqm", "has_mamad", "has_balcony",
            "has_rooftop", "pets_allowed", "is_furnished", "is_renovated",
            "source_platform", "publication_date", "contact_phone", "ad_url",
            "unique_id", "price", "rooms", "available_from", "status",
            "last_verified", "added_by_agent", "is_broker"
        ]

        result_list = []
        for i, row in enumerate(rows[HEADER_ROW:], start=HEADER_ROW + 1):
            padded = row + [""] * (len(keys) - len(row))
            d = dict(zip(keys, padded))
            d["_row_number"] = i
            result_list.append(d)

        return result_list
