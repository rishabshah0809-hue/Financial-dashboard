#!/usr/bin/env python3
"""
build_sector_snapshot.py
------------------------
Generates data/sector_snapshot.json for FundaCheck's Sector lens.

Pipeline (runs in the monthly GitHub Action, or locally with your key):

    NSE Indices constituent CSV  →  IndianAPI /stock (one call per company)
      →  pooled sector aggregation  →  data/sector_snapshot.json

The Streamlit app only READS that JSON; it never calls IndianAPI.

Key safety: the IndianAPI key is read only from the INDIANAPI_KEY environment
variable (a GitHub Actions secret in CI). It is never printed, never written to
the JSON, never committed.

Usage:
    export INDIANAPI_KEY=...                     # PowerShell: $env:INDIANAPI_KEY=...
    python scripts/build_sector_snapshot.py                      # all sectors
    python scripts/build_sector_snapshot.py --sectors banking,it_services
    python scripts/build_sector_snapshot.py --limit 12          # cap constituents/sector
    python scripts/build_sector_snapshot.py --dry-run           # NSE only, 0 API calls

Monthly cadence keeps usage well under the IndianAPI free 500-credit plan
(~1 credit per /stock call; a full 9-sector refresh is ~120-180 calls).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# Allow running as a script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import indianapi as API                       # noqa: E402
from core.sector_aggregate import aggregate_sector, new_snapshot  # noqa: E402
from core.sector_universe import UNIVERSE, parse_constituents     # noqa: E402

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "sector_snapshot.json"
NSE_HEADERS = {"User-Agent": "Mozilla/5.0 (FundaCheck sector snapshot builder)"}
PACING_SECONDS = 0.4      # gentle pacing under the 25 req/s limit


def fetch_constituents(uni) -> list:
    try:
        r = requests.get(uni.csv_url, headers=NSE_HEADERS, timeout=30)
        if r.status_code != 200:
            print(f"  ! NSE CSV {uni.csv_file}: HTTP {r.status_code}")
            return []
        rows = parse_constituents(r.text)
        print(f"  NSE {uni.index_label}: {len(rows)} constituents")
        return rows
    except requests.RequestException as e:
        print(f"  ! NSE CSV fetch failed for {uni.index_label}: {type(e).__name__}")
        return []


def build(sectors: list[str], limit: int | None, dry_run: bool) -> tuple[dict, int]:
    key = os.environ.get("INDIANAPI_KEY", "").strip()
    if not key and not dry_run:
        sys.exit("INDIANAPI_KEY not set. Export it first, or use --dry-run.")

    snapshot = new_snapshot()
    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    calls = 0

    for skey in sectors:
        uni = UNIVERSE[skey]
        print(f"\n=== {uni.name}  ({uni.index_label}) ===")
        constituents = fetch_constituents(uni)
        if limit:
            constituents = constituents[:limit]
        attempted = len(constituents)
        companies = []
        if not dry_run:
            for con in constituents:
                try:
                    companies.append(API.get_company(con.symbol, con.isin, con.company, key))
                except API.IndianAPIError as e:
                    print(f"    - {con.symbol}: fetch failed ({e.kind})")
                    if e.kind in ("rate_limited", "auth"):
                        print("    !! hard stop (rate/credit or auth). Aborting further calls.")
                        snapshot["_aborted"] = e.kind
                        snap = aggregate_sector(uni, companies, attempted, as_of)
                        snapshot["sectors"][skey] = snap
                        return snapshot, calls
                finally:
                    calls += 1
                    time.sleep(PACING_SECONDS)
        snap = aggregate_sector(uni, companies, attempted, as_of)
        snapshot["sectors"][skey] = snap
        m = snap["metrics"]
        print(f"    included {snap['constituents_included']}/{attempted} | "
              f"PE={m['pe']['value']} PB={m['pb']['value']} ROE={m['roe']['value']} "
              f"ROA={m['roa']['value']} ROCE={m['roce'].get('value')}"
              f"{' (N/A)' if not m['roce'].get('applicable', True) else ''}")

    snapshot["api_calls_used"] = calls
    return snapshot, calls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sectors", default="", help="comma list of sector keys; default all")
    ap.add_argument("--limit", type=int, default=None, help="cap constituents per sector")
    ap.add_argument("--dry-run", action="store_true", help="NSE only, no IndianAPI calls")
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    sectors = [s.strip() for s in args.sectors.split(",") if s.strip()] or list(UNIVERSE)
    unknown = [s for s in sectors if s not in UNIVERSE]
    if unknown:
        sys.exit(f"Unknown sector keys: {unknown}. Valid: {list(UNIVERSE)}")

    snapshot, calls = build(sectors, args.limit, args.dry_run)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    print(f"\nWrote {out}  |  IndianAPI calls used: {calls}")
    print("(Check your IndianAPI dashboard to confirm credits consumed per call.)")


if __name__ == "__main__":
    main()
