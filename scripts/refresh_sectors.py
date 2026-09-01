#!/usr/bin/env python
"""
refresh_sectors.py
------------------
The monthly Sector Lens refresh. Runs once a month in GitHub Actions (and on
``workflow_dispatch`` for manual testing). Streamlit never runs this — it only
reads the snapshot this job writes.

Pipeline
========
    NSE constituent lists  (data/nse_constituents/*.csv)
      → de-duplicate across all nine sectors
      → count unique companies and expected IndianAPI requests
      → ABORT if the count exceeds the 400 safety threshold  (500-credit plan)
      → fetch each unique company from IndianAPI exactly once
      → pooled sector metrics  (P/E, P/B, ROE, ROA, ROCE-where-meaningful)
      → Top 10 by real market cap, reusing the same responses (zero extra calls)
      → month-over-month changes vs the previous snapshot
      → current monthly tilt from real deltas
      → write data/sector_snapshot.json  (atomically)

Safety
======
* **Pre-flight guard.** If the de-duplicated universe would need more than
  ``MAX_REQUESTS`` (400) calls, the job aborts *before making any request* and
  the previous snapshot is left untouched.
* **Failure protection.** The new snapshot is only written when the run produces
  at least one valid sector. On any hard failure the previous
  ``data/sector_snapshot.json`` is preserved exactly — never overwritten with
  empty, partial, or fabricated data.
* **No fabrication.** Missing inputs become ``null`` (rendered "—"); a company
  that can't be fetched or fails the symbol check is counted as skipped with a
  reason and left out of the aggregates.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

# make the repo root importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import indianapi as E          # noqa: E402
from core import sector_universe as U     # noqa: E402
from core import tilt as T                # noqa: E402

LOGGER = logging.getLogger("fundacheck.refresh")

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = ROOT / "data" / "sector_snapshot.json"
MASTER_CSV = ROOT / "data" / "company_master.csv"

MAX_REQUESTS = 400          # hard safety threshold (plan is 500/month)
TOP_N = 10
SOURCE = "IndianAPI + NSE"
REFRESH_FREQUENCY = "monthly"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _load_name_map() -> dict[str, str]:
    """NSE symbol → company name, from the committed master list. IndianAPI's
    /stock endpoint is queried by name; the symbol on the response is still
    verified by the client."""
    out: dict[str, str] = {}
    if not MASTER_CSV.exists():
        return out
    with MASTER_CSV.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            sym = (row.get("nse_symbol") or "").strip().upper()
            name = (row.get("name") or "").strip()
            if sym and name:
                out[sym] = name
    return out


def _load_previous() -> dict | None:
    if not SNAPSHOT_PATH.exists():
        return None
    try:
        return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _prev_sector(prev: dict | None, key: str) -> dict | None:
    if not prev:
        return None
    for s in prev.get("sectors", []):
        if s.get("key") == key:
            return s
    return None


def _mom_changes(cur: dict, prev: dict | None) -> dict:
    """Month-over-month deltas, only where both months have a real value."""
    if not prev:
        return {}
    out = {}
    cm, pm = cur.get("metrics", {}), prev.get("metrics", {})
    for k in ("pe", "pb", "roe", "roa", "roce"):
        a, b = cm.get(k), pm.get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            out[k] = {"from": b, "to": a, "change": round(a - b, 2)}
    # Top-10 membership churn
    cur_syms = {r["nse_symbol"] for r in cur.get("top10", [])}
    prev_syms = {r["nse_symbol"] for r in prev.get("top10", [])}
    if prev_syms:
        entered = sorted(cur_syms - prev_syms)
        left = sorted(prev_syms - cur_syms)
        out["top10"] = {"entered": entered, "left": left,
                        "changed": len(entered) + len(left)}
    return out


def _sector_earnings_growth(companies: list[E.Company]) -> float | None:
    """A pooled earnings-growth proxy for the tilt engine: Σ EPS-weighted... we
    keep it simple and robust — the median of available per-company EPS-TTM
    growth, only when enough real values exist. None otherwise (never faked)."""
    vals = [c.ratios.get("eps_ttm_growth") for c in companies
            if isinstance(c.ratios.get("eps_ttm_growth"), (int, float))]
    if len(vals) < 2:
        return None
    vals.sort()
    mid = len(vals) // 2
    return round((vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2), 2)


def _sector_revenue_growth(companies: list[E.Company]) -> float | None:
    vals = [c.ratios.get("op_rev_growth_ttm") for c in companies
            if isinstance(c.ratios.get("op_rev_growth_ttm"), (int, float))]
    if len(vals) < 2:
        return None
    vals.sort()
    mid = len(vals) // 2
    return round((vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2), 2)


# ---------------------------------------------------------------------------
# core run
# ---------------------------------------------------------------------------
def build_snapshot(fetch, per_sector: dict[str, list[str]], union: list[str],
                   name_map: dict[str, str], previous: dict | None,
                   request_stats: dict) -> dict:
    """Fetch the union once, then assemble all nine sectors. ``fetch`` is a
    callable (symbol, name) -> raw dict | None so this is testable offline."""
    # ---- fetch each unique company exactly once ----
    parsed: dict[str, E.Company] = {}
    skipped_fetch: dict[str, str] = {}
    for sym in union:
        name = name_map.get(sym, sym)
        raw = fetch(sym, name)
        request_stats["actual"] += 1
        if raw is None:
            request_stats["failed"] += 1
            skipped_fetch[sym] = "fetch_failed"
            continue
        comp = E.parse_company(raw)
        if comp is None:
            request_stats["failed"] += 1
            skipped_fetch[sym] = "unparseable"
            continue
        request_stats["successful"] += 1
        parsed[sym] = comp

    # ---- assemble each sector from the shared pool (no extra calls) ----
    sectors_out = []
    for key in U.ORDER:
        uni = U.UNIVERSES[key]
        syms = per_sector.get(key, [])
        companies, skipped = [], []
        for s in syms:
            if s in parsed:
                companies.append(parsed[s])
            else:
                skipped.append({"symbol": s,
                                "reason": skipped_fetch.get(s, "not_fetched")})

        metrics = E.pooled_metrics(companies, is_financial=uni.is_financial)
        top10 = E.rank_top(companies, TOP_N, is_financial=uni.is_financial)
        # financial period = the modal latest fiscal year among constituents
        years = [c.fiscal_year for c in companies if c.fiscal_year]
        period = max(set(years), key=years.count) if years else None

        sector = {
            "key": key,
            "sector_name": uni.sector_name,
            "reference_index": uni.reference_index,
            "mapping_type": uni.mapping_type,
            "mapping_note": uni.mapping_note,
            "is_financial": uni.is_financial,
            "constituent_count": len(syms),
            "included_count": len(companies),
            "skipped_count": len(skipped),
            "skipped": skipped,
            "financial_period": f"FY{period}" if period else None,
            "metrics": metrics,
            "earnings_growth": _sector_earnings_growth(companies),
            "revenue_growth": _sector_revenue_growth(companies),
            "top10": top10,
        }
        prev_sec = _prev_sector(previous, key)
        sector["monthly_changes"] = _mom_changes(sector, prev_sec)
        sector.update(T.current_tilt(key, sector, prev_sec))
        sectors_out.append(sector)

    now = dt.datetime.now(dt.timezone.utc)
    prev_date = (previous or {}).get("as_of_date")
    snapshot = {
        "generated_at": now.isoformat(),
        "as_of_date": now.date().isoformat(),
        "previous_snapshot_date": prev_date,
        "refresh_frequency": REFRESH_FREQUENCY,
        "source": SOURCE,
        "expected_api_requests": request_stats["expected"],
        "actual_api_requests": request_stats["actual"],
        "successful_requests": request_stats["successful"],
        "failed_requests": request_stats["failed"],
        "skipped_requests": request_stats["expected"] - request_stats["actual"],
        "safety_threshold": MAX_REQUESTS,
        "methodology": E.METHODOLOGY,
        "sectors": sectors_out,
    }
    return snapshot


def validate(snapshot: dict) -> list[str]:
    """Return a list of validation problems ([] == valid). Used as a gate before
    writing — a snapshot with no usable sector is rejected so the previous one
    survives."""
    problems = []
    sectors = snapshot.get("sectors", [])
    if len(sectors) != len(U.ORDER):
        problems.append(f"expected {len(U.ORDER)} sectors, got {len(sectors)}")
    usable = [s for s in sectors if s.get("included_count", 0) > 0]
    if not usable:
        problems.append("no sector has any included company")
    for s in sectors:
        if s.get("is_financial") and s.get("metrics", {}).get("roce") is not None:
            problems.append(f"{s['key']}: financial sector must have ROCE = null")
    return problems


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ---------------------------------------------------------------------------
# fixture fetcher (offline validation) — reads saved raw dumps, never network
# ---------------------------------------------------------------------------
def _fixture_fetcher(mapping: dict[str, Path]):
    cache = {sym: json.loads(p.read_text(encoding="utf-8"))
             for sym, p in mapping.items()}

    def fetch(symbol: str, name: str):
        return cache.get(symbol)
    return fetch


def run(*, fixtures: bool = False) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    previous = _load_previous()
    name_map = _load_name_map()

    if fixtures:
        # A real two-sector validation universe from the saved dumps.
        fx = {
            "HDFCBANK": ROOT / "api" / "indianapi_hdfc_raw.json",
            "TCS": ROOT / "api" / "indianapi_tcs_raw.json",
        }
        # allow the HDFC dump to live in Downloads (as delivered)
        dl = Path(os.path.expanduser("~")) / "Downloads" / "indianapi_hdfc_raw.json"
        if not fx["HDFCBANK"].exists() and dl.exists():
            fx["HDFCBANK"] = dl
        fx = {k: v for k, v in fx.items() if v.exists()}
        per_sector = {k: [] for k in U.ORDER}
        per_sector["banking"] = [s for s in ("HDFCBANK",) if s in fx]
        per_sector["it_services"] = [s for s in ("TCS",) if s in fx]
        union = list(fx.keys())
        fetch = _fixture_fetcher(fx)
        name_map = name_map or {"HDFCBANK": "HDFC Bank", "TCS": "Tata Consultancy Services"}
    else:
        from core.indianapi_client import Fetcher, has_key
        if not has_key():
            LOGGER.error("INDIANAPI_KEY not set — aborting, previous snapshot kept.")
            return 2
        per_sector, union = U.all_unique_symbols()
        if not union:
            LOGGER.error("No NSE constituents found in data/nse_constituents/ — "
                         "aborting, previous snapshot kept.")
            return 2
        fetcher = Fetcher()
        fetch = fetcher.fetch

    expected = len(union)
    LOGGER.info("Unique companies to fetch: %d (threshold %d)", expected, MAX_REQUESTS)
    if expected > MAX_REQUESTS:
        LOGGER.error("Expected %d requests exceeds safety threshold %d — ABORTING "
                     "with zero API calls; previous snapshot kept.",
                     expected, MAX_REQUESTS)
        return 3

    stats = {"expected": expected, "actual": 0, "successful": 0, "failed": 0}
    snapshot = build_snapshot(fetch, per_sector, union, name_map, previous, stats)

    problems = validate(snapshot)
    if problems:
        LOGGER.error("Validation failed, previous snapshot kept:\n  - %s",
                     "\n  - ".join(problems))
        return 4

    _atomic_write(SNAPSHOT_PATH, snapshot)
    LOGGER.info("Wrote %s — %d/%d requests succeeded across %d sectors.",
                SNAPSHOT_PATH, stats["successful"], stats["actual"],
                len(snapshot["sectors"]))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Monthly Sector Lens refresh.")
    ap.add_argument("--fixtures", action="store_true",
                    help="Offline validation run from saved raw dumps (no network).")
    args = ap.parse_args()
    return run(fixtures=args.fixtures)


if __name__ == "__main__":
    raise SystemExit(main())
