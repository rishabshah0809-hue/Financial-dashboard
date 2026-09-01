#!/usr/bin/env python
"""
fetch_nse_constituents.py
-------------------------
Download official NSE index constituent CSVs into data/nse_constituents/<sector_key>.csv
so the monthly refresh pipeline has an authoritative, up-to-date membership backbone.

Features:
- Membership from official NSE constituent lists published on niftyindices.com.
- Fail-soft: if an index CSV fails to download, any existing local CSV is preserved.
- Only overwrites with valid CSV containing a Symbol column.
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import sector_universe as U  # noqa: E402

DEST = ROOT / "data" / "nse_constituents"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*;q=0.8",
}
TIMEOUT = 25


def _valid_csv(text: str) -> bool:
    try:
        reader = csv.DictReader(io.StringIO(text))
        cols = [c.strip().lower() for c in (reader.fieldnames or []) if c]
        if "symbol" not in cols:
            return False
        # Ensure at least one non-empty symbol row
        sym_col = reader.fieldnames[cols.index("symbol")]
        return any((row.get(sym_col) or "").strip() for row in reader)
    except Exception:
        return False


def fetch_one(uni: U.SectorUniverse) -> bool:
    try:
        resp = requests.get(uni.source_csv_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as exc:
        print(f"  [{uni.key}] network error ({type(exc).__name__}) — kept existing")
        return False

    if resp.status_code != 200 or not resp.text:
        print(f"  [{uni.key}] HTTP {resp.status_code} — kept existing")
        return False

    if not _valid_csv(resp.text):
        print(f"  [{uni.key}] invalid constituent CSV format — kept existing")
        return False

    DEST.mkdir(parents=True, exist_ok=True)
    uni.csv_path.write_text(resp.text, encoding="utf-8")
    print(f"  [{uni.key}] refreshed {uni.reference_index}")
    return True


def main() -> int:
    print(f"Refreshing NSE constituents into {DEST}")
    DEST.mkdir(parents=True, exist_ok=True)
    ok = 0
    for key in U.ORDER:
        uni = U.UNIVERSES[key]
        if fetch_one(uni):
            ok += 1
    total = len(U.ORDER)
    print(f"Refreshed {ok}/{total} constituent lists.")
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

