"""
models.py — The Listing dataclass.
This is the single shared data structure used by all scrapers and the Sheets writer.
Every scraper must produce a list of Listing objects.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Listing:
    """
    Represents a single apartment listing scraped from any source.
    Field names match the Google Sheets column schema defined in the Tech Spec.
    """

    # ── Core fields (always required) ─────────────────────────────────────────
    address:          str             # Column A — Full address
    source_platform:  str             # Column J — e.g. "Yad2", "Madlan", "Facebook"
    ad_url:           str             # Column M — Direct URL to the original listing
    price:            Optional[int]   # Column O — Monthly rent in NIS
    rooms:            Optional[float] # Column P — Number of rooms (e.g. 2.5)

    # ── Optional fields (extracted when available) ────────────────────────────
    floor:            Optional[int]   = None   # Column B
    size_sqm:         Optional[int]   = None   # Column C
    has_mamad:        Optional[bool]  = None   # Column D
    has_balcony:      Optional[bool]  = None   # Column E
    has_rooftop:      Optional[bool]  = None   # Column F
    pets_allowed:     Optional[bool]  = None   # Column G
    is_furnished:     Optional[bool]  = None   # Column H
    is_renovated:     Optional[bool]  = None   # Column I
    contact_phone:    Optional[str]   = None   # Column L
    available_from:   Optional[str]   = None   # Column Q — Date string "DD/MM/YYYY"
    is_broker:        Optional[bool]  = None   # Column U — True if posted by a broker/agent (מתווך)

    # ── Auto-populated fields (set by the database writer, not the scraper) ───
    # publication_date can be set by scraper if available, defaults to now
    publication_date: str = field(default_factory=lambda: datetime.now().strftime("%d/%m/%Y"))
    status:           str = "Active"            # Column R
    last_verified:    str = field(default_factory=lambda: datetime.now().strftime("%d/%m/%Y"))
    added_by_agent:   str = "Agent-1A"          # Column T

    def _is_newer_than(self, cutoff_dt: datetime) -> bool:
        """
        Check if this listing was published after cutoff_dt.
        Used for incremental scanning.

        publication_date is stored as "DD/MM/YYYY" string in the listing.
        """
        try:
            listing_dt = datetime.strptime(self.publication_date, "%d/%m/%Y")
            return listing_dt >= cutoff_dt
        except Exception:
            # If date parsing fails, assume it's new (be conservative)
            return True

    def to_sheet_row(self) -> list:
        """
        Converts this Listing to a flat list matching the Google Sheets column order.
        Columns A through T as defined in the Tech Spec.
        """
        def yesno(val: Optional[bool]) -> str:
            if val is True:
                return "כן"
            if val is False:
                return "לא"
            return ""

        return [
            self.address,                        # A — כתובת
            self.floor if self.floor is not None else "",   # B — קומה
            self.size_sqm if self.size_sqm is not None else "",  # C — מ"ר
            yesno(self.has_mamad),               # D — ממ"ד
            yesno(self.has_balcony),             # E — מרפסת
            yesno(self.has_rooftop),             # F — גג
            yesno(self.pets_allowed),            # G — בע"ח
            yesno(self.is_furnished),            # H — מרוהטת
            yesno(self.is_renovated),            # I — משופצת
            self.source_platform,                # J — פלטפורמה
            self.publication_date,               # K — תאריך פרסום
            self.contact_phone or "",            # L — טלפון
            self.ad_url,                         # M — קישור
            "",                                  # N — מזהה (auto-assigned by Sheets writer)
            self.price if self.price is not None else "",    # O — מחיר
            self.rooms if self.rooms is not None else "",    # P — חדרים
            self.available_from or "",           # Q — כניסה מ-
            self.status,                         # R — סטטוס
            self.last_verified,                  # S — תאריך בדיקה
            self.added_by_agent,                 # T — הוסף על ידי
            yesno(self.is_broker),               # U — מתווך
        ]
