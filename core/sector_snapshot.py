"""
sector_snapshot.py
------------------
Read side of Sector Lens. Streamlit imports this to load the monthly
precomputed IndianAPI + NSE snapshot (data/sector_snapshot.json).

Never makes live network calls, never scrapes, and never computes at request time.
If the snapshot is missing or unreadable, returns None cleanly.
"""

from __future__ import annotations

import datetime as _dt
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


SCREENER_PATH = Path(__file__).resolve().parent.parent / "data" / "screener_snapshot.json"


def load_screener(path: Path | None = None) -> dict | None:
    """Load the daily Screener market snapshot (read-only). None if missing/invalid."""
    p = path or SCREENER_PATH
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) and data.get("sectors") else None


def merge_sector(screener_snap: dict | None, fundamentals_snap: dict | None,
                 sector_key: str) -> tuple[dict | None, dict]:
    """Hybrid sector view: Screener daily (CMP + bottom-up P/E,P/B,ROE,ROCE and the
    comparison table) overlaid with the periodic IndianAPI 'fundamentals' snapshot
    (PEG, EPS growth, Piotroski, ROA and the richer columns).

    Returns (merged_sector_or_None, source_meta). Never fabricates: a field absent
    in both stays absent. If Screener is missing, the IndianAPI sector is returned
    as-is (clearly attributed via source_meta)."""
    scr = get_sector(screener_snap, sector_key) if screener_snap else None
    fund = get_sector(fundamentals_snap, sector_key) if fundamentals_snap else None

    mkt_date = (screener_snap or {}).get("market_snapshot_date") if scr else None
    market_stale = bool(mkt_date) and mkt_date != _dt.date.today().isoformat()
    meta = {
        "market_source": "Screener.in" if scr else None,
        "market_snapshot_date": mkt_date,
        "market_stale": market_stale,                       # today's refresh missing -> stale layer
        "market_stale_count": (scr or {}).get("stale_count", 0) if scr else None,
        "fundamentals_source": "IndianAPI + NSE" if fund else None,
        "fundamentals_date": (fundamentals_snap or {}).get("as_of_date") if fund else None,
        "fundamental_period": (scr or {}).get("fundamental_period") or (fund or {}).get("financial_period"),
    }

    if not scr and not fund:
        return None, meta
    if not scr:
        return fund, meta            # Screener unavailable -> IndianAPI only
    if not fund:
        return scr, meta             # only Screener available

    # metrics: Screener headline valuation/returns + IndianAPI depth tiles
    fm = fund.get("metrics", {})
    sm = scr.get("metrics", {})
    merged_metrics = dict(fm)        # start with IndianAPI (peg/eps/piotroski/roa/...)
    for k in ("pe", "pb", "roe", "roce"):
        merged_metrics[k] = sm.get(k)          # Screener daily bottom-up wins
    merged_metrics["methodology"] = sm.get("methodology")

    # constituents: Screener row (daily CMP + core) overlaid with IndianAPI extras
    fund_rows = {r.get("nse_symbol"): r for r in fund.get("constituents", [])}
    merged_rows = []
    for r in scr.get("constituents", []):
        base = dict(fund_rows.get(r.get("nse_symbol"), {}))   # roa/opm/npm/de/... if present
        base.update({k: v for k, v in r.items() if v is not None or k not in base})
        merged_rows.append(base)
    for i, r in enumerate(sorted(merged_rows, key=lambda x: (x.get("market_cap") or 0), reverse=True), 1):
        r["rank"] = i

    merged = dict(fund)              # keep tilt / seasonality / mom / names from IndianAPI
    merged["metrics"] = merged_metrics
    merged["constituents"] = merged_rows
    merged["included_count"] = scr.get("included_count", len(merged_rows))
    merged["constituent_count"] = scr.get("constituent_count", merged.get("constituent_count"))
    merged["earnings_growth"] = fund.get("earnings_growth")
    return merged, meta


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

