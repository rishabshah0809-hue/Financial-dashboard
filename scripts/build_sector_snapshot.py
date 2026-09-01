#!/usr/bin/env python3
"""
build_sector_snapshot.py
------------------------
Generates data/sector_snapshot.json for FundaCheck's Sector lens — ALL 9 sectors
in a single monthly run.

Pipeline:
    NSE Indices constituent CSVs (all sectors)
      → de-duplicate companies across indices (fetch each ISIN once)
      → credit safety check (abort if unique companies exceed the budget)
      → IndianAPI /stock (one call per UNIQUE company)
      → pooled sector aggregation (all sectors)
      → validate → write data/sector_snapshot.json

The Streamlit app only READS the JSON; it never calls IndianAPI. Runs in the
monthly GitHub Action, or locally with your key.

Credit safety: the IndianAPI free plan has 500 credits/month. This script counts
UNIQUE companies first and ABORTS before making any call if that exceeds
--max-budget (default 400), so a run can never exhaust the plan. Failures never
overwrite a previous good snapshot (validate-before-write).

Key safety: INDIANAPI_KEY is read only from the environment (a GitHub Actions
secret in CI). It is never printed, never written to the JSON, never committed.

Usage:
    export INDIANAPI_KEY=...                         # PowerShell: $env:INDIANAPI_KEY=...
    python scripts/build_sector_snapshot.py                          # all 9 sectors
    python scripts/build_sector_snapshot.py --sectors banking,it_services
    python scripts/build_sector_snapshot.py --limit 8               # cap per sector (testing)
    python scripts/build_sector_snapshot.py --dry-run               # NSE only, 0 API calls
    python scripts/build_sector_snapshot.py --max-budget 400        # hard credit ceiling
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import indianapi as API                                 # noqa: E402
from core.sector_aggregate import aggregate_sector, new_snapshot  # noqa: E402
from core.sector_universe import UNIVERSE, parse_constituents     # noqa: E402

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "sector_snapshot.json"
NSE_HEADERS = {"User-Agent": "Mozilla/5.0 (FundaCheck sector snapshot builder)"}
PACING_SECONDS = 0.4
DEFAULT_MAX_BUDGET = 400        # hard ceiling below the 500-credit free plan


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


def validate(snapshot: dict) -> tuple[bool, str]:
    """A snapshot is valid only if real data actually came back."""
    if snapshot.get("successful_requests", 0) <= 0:
        return False, "no successful IndianAPI responses"
    sectors_with_data = 0
    for s in snapshot.get("sectors", {}).values():
        m = s.get("metrics", {})
        if any((m.get(k) or {}).get("value") is not None for k in ("pe", "pb", "roe", "roa")):
            sectors_with_data += 1
    if sectors_with_data == 0:
        return False, "no sector produced any headline metric"
    return True, f"{sectors_with_data} sector(s) have data"


def build(sectors: list[str], limit: int | None, dry_run: bool, max_budget: int):
    key = os.environ.get("INDIANAPI_KEY", "").strip()
    if not key and not dry_run:
        sys.exit("INDIANAPI_KEY not set. Export it first, or use --dry-run.")

    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot = new_snapshot()
    snapshot["as_of_date"] = as_of
    snapshot["refresh_frequency"] = "monthly"

    # ---- 1) constituent lists for every sector ----
    per_sector: dict[str, list] = {}
    for skey in sectors:
        uni = UNIVERSE[skey]
        print(f"\n=== {uni.name}  ({uni.index_label}) ===")
        rows = fetch_constituents(uni)
        if limit:
            rows = rows[:limit]
        per_sector[skey] = rows

    # ---- 2) de-duplicate companies across sectors (fetch each ISIN once) ----
    unique: dict[str, object] = {}
    for rows in per_sector.values():
        for con in rows:
            unique.setdefault(con.isin, con)
    expected = len(unique)
    print(f"\nUnique companies across all sectors: {expected} "
          f"(budget ceiling {max_budget})")
    snapshot["unique_companies"] = expected
    snapshot["expected_requests"] = 0 if dry_run else expected
    snapshot["budget_ceiling"] = max_budget

    # ---- 3) credit safety: abort BEFORE any call if over budget ----
    if not dry_run and expected > max_budget:
        print(f"!! ABORT: {expected} unique companies exceed the {max_budget} "
              "credit ceiling. No API calls were made; previous snapshot kept.")
        return None, {"expected": expected, "aborted": True}

    # ---- 4) fetch each unique company once ----
    cache: dict[str, object] = {}
    actual = successful = failed = 0
    if not dry_run:
        for isin, con in unique.items():
            try:
                c = API.get_company(con.symbol, con.isin, con.company, key)
                cache[isin] = c
                successful += 1
            except API.IndianAPIError as e:
                cache[isin] = None
                failed += 1
                print(f"    - {con.symbol}: fetch failed ({e.kind})")
                if e.kind in ("rate_limited", "auth"):
                    print("    !! hard stop (rate/credit or auth). Aborting run; "
                          "previous snapshot kept.")
                    return None, {"expected": expected, "actual": actual + 1,
                                  "successful": successful, "failed": failed,
                                  "aborted": True}
            finally:
                actual += 1
                time.sleep(PACING_SECONDS)

    skipped = expected - actual if not dry_run else 0
    snapshot["total_api_requests"] = actual
    snapshot["successful_requests"] = successful
    snapshot["failed_requests"] = failed
    snapshot["skipped_requests"] = skipped

    # ---- 5) aggregate every sector from the shared cache ----
    for skey in sectors:
        uni = UNIVERSE[skey]
        rows = per_sector[skey]
        companies = [cache[c.isin] for c in rows if cache.get(c.isin) is not None]
        snap = aggregate_sector(uni, companies, attempted=len(rows), as_of=as_of)
        snapshot["sectors"][skey] = snap
        m = snap["metrics"]
        print(f"  {uni.name}: included {snap['constituents_included']}/{len(rows)} | "
              f"PE={m['pe']['value']} PB={m['pb']['value']} ROE={m['roe']['value']} "
              f"ROA={m['roa']['value']} ROCE={m['roce'].get('value')}"
              f"{' (N/A)' if not m['roce'].get('applicable', True) else ''}")

    stats = {"expected": expected, "actual": actual, "successful": successful,
             "failed": failed, "skipped": skipped}
    return snapshot, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sectors", default="", help="comma list of sector keys; default all 9")
    ap.add_argument("--limit", type=int, default=None, help="cap constituents per sector")
    ap.add_argument("--dry-run", action="store_true", help="NSE only, 0 API calls")
    ap.add_argument("--max-budget", type=int, default=DEFAULT_MAX_BUDGET)
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    sectors = [s.strip() for s in args.sectors.split(",") if s.strip()] or list(UNIVERSE)
    unknown = [s for s in sectors if s not in UNIVERSE]
    if unknown:
        sys.exit(f"Unknown sector keys: {unknown}. Valid: {list(UNIVERSE)}")

    snapshot, stats = build(sectors, args.limit, args.dry_run, args.max_budget)

    if snapshot is None:
        # Aborted (over budget / hard stop): keep the previous good snapshot.
        print(f"\nNo snapshot written (aborted). Stats: {stats}")
        sys.exit(2)

    ok, why = validate(snapshot) if not args.dry_run else (True, "dry-run")
    if not ok:
        print(f"\nVALIDATION FAILED: {why}. Previous snapshot kept; nothing written.")
        sys.exit(3)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    print(f"\nWrote {out}")
    print(f"Validation: {why}")
    print(f"API calls — expected {stats['expected']}, actual {stats['actual']}, "
          f"successful {stats['successful']}, failed {stats['failed']}, "
          f"skipped {stats['skipped']}")
    print("(Confirm credits consumed on your IndianAPI dashboard.)")


if __name__ == "__main__":
    main()
