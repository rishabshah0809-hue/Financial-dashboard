"""
sector_snapshot.py
------------------
The read side of the Sector Lens. Streamlit imports this and *only* this — it
never calls IndianAPI or NSE, never scrapes, and never computes financials at
request time. It just loads the JSON the monthly job produced and hands the
right sector to the UI.

If the snapshot file is missing or unreadable the reader returns ``None`` and
the Lens falls back to its previous behaviour (the live Trendlyne path) — it
does not invent data.
"""

from __future__ import annotations

import json
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "sector_snapshot.json"


def load_snapshot(path: Path | None = None) -> dict | None:
    p = path or SNAPSHOT_PATH
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) and data.get("sectors") else None


def get_sector(snapshot: dict | None, sector_key: str) -> dict | None:
    if not snapshot:
        return None
    for s in snapshot.get("sectors", []):
        if s.get("key") == sector_key:
            return s
    return None


def snapshot_meta(snapshot: dict | None) -> dict:
    """The header fields the Lens shows: source, last-updated, data period."""
    if not snapshot:
        return {}
    return {
        "source": snapshot.get("source"),
        "as_of_date": snapshot.get("as_of_date"),
        "previous_snapshot_date": snapshot.get("previous_snapshot_date"),
        "refresh_frequency": snapshot.get("refresh_frequency"),
        "actual_api_requests": snapshot.get("actual_api_requests"),
        "successful_requests": snapshot.get("successful_requests"),
    }
