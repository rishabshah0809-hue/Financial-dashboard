"""
sector_snapshot.py
------------------
Read side of Sector Lens. Streamlit imports this to load the monthly
precomputed IndianAPI + NSE snapshot (data/sector_snapshot.json).

Never makes live network calls, never scrapes, and never computes at request time.
If the snapshot is missing or unreadable, returns None cleanly.
"""

from __future__ import annotations

import json
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "sector_snapshot.json"


def load_snapshot(path: Path | None = None) -> dict | None:
    """Load and validate the sector snapshot JSON file. Returns None if missing/invalid."""
    p = path or SNAPSHOT_PATH
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None

    if not isinstance(data, dict):
        return None
    sectors = data.get("sectors")
    if not sectors:
        return None
    return data


def get_sector(snapshot: dict | None, sector_key: str) -> dict | None:
    """Retrieve data for a specific sector from the snapshot dict."""
    if not snapshot:
        return None
    sectors = snapshot.get("sectors")
    if isinstance(sectors, list):
        for s in sectors:
            if isinstance(s, dict) and s.get("key") == sector_key:
                return s
    elif isinstance(sectors, dict):
        return sectors.get(sector_key)
    return None


def snapshot_meta(snapshot: dict | None) -> dict:
    """Return top-level metadata from the snapshot."""
    if not snapshot or not isinstance(snapshot, dict):
        return {}
    return {
        "source": snapshot.get("source", "IndianAPI (fundamentals) + NSE Indices (constituents)"),
        "generated_at": snapshot.get("generated_at"),
        "as_of_date": snapshot.get("as_of_date"),
        "previous_snapshot_date": snapshot.get("previous_snapshot_date"),
        "refresh_frequency": snapshot.get("refresh_frequency", "monthly"),
        "expected_api_requests": snapshot.get("expected_api_requests"),
        "actual_api_requests": snapshot.get("actual_api_requests"),
        "successful_requests": snapshot.get("successful_requests"),
        "failed_requests": snapshot.get("failed_requests"),
        "safety_threshold": snapshot.get("safety_threshold", 400),
    }

