#!/usr/bin/env python
"""
fetch_nse_constituents.py
-------------------------
Download the official NSE index constituent CSVs into
``data/nse_constituents/<sector_key>.csv`` so the monthly refresh has an
up-to-date membership backbone.

Rules
=====
* **Membership from NSE only.** Each list is the one NSE publishes for that
  index (``source_csv_url`` in ``core.sector_universe``). We do not derive
  membership from IndianAPI's industry field, and we never write a fabricated
  list.
* **Fail-soft.** A list that can't be downloaded (network, block, non-200) is
  simply left as-is — any previously committed CSV stays in place. The exit code
  reflects whether *every* list refreshed, but the monthly job continues with
  whatever lists are available.
* **Only overwrite on a good CSV.** A response is written only when it parses as
  a CSV containing a ``Symbol`` column with at least one row, so a bad response
  can never blank out a good committed list.
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import sector_universe as U   # noqa: E402

DEST = Path(__file__).resolve().parent.parent / "data" / "nse_constituents"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "text/csv,*/*;q=0.8",
}
TIMEOUT = 20


def _valid_csv(text: str) -> bool:
    reader = csv.DictReader(io.StringIO(text))
    cols = [c.strip().lower() for c in (reader.fieldnames or [])]
    if "symbol" not in cols:
        return False
    return any((row.get(reader.fieldnames[cols.index("symbol")]) or "").strip()
               for row in reader)


def fetch_one(uni) -> bool:
    try:
        resp = requests.get(uni.source_csv_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as exc:
        print(f"  [{uni.key}] network error: {type(exc).__name__} — kept existing")
        return False
    if resp.status_code != 200 or not resp.text:
        print(f"  [{uni.key}] HTTP {resp.status_code} — kept existing")
        return False
    if not _valid_csv(resp.text):
        print(f"  [{uni.key}] response is not a valid constituent CSV — kept existing")
        return False
    DEST.mkdir(parents=True, exist_ok=True)
    uni.csv_path.write_text(resp.text, encoding="utf-8")
    print(f"  [{uni.key}] refreshed {uni.reference_index}")
    return True


def main() -> int:
    print(f"Refreshing NSE constituents into {DEST}")
    ok = 0
    for key in U.ORDER:
        if fetch_one(U.UNIVERSES[key]):
            ok += 1
    total = len(U.ORDER)
    print(f"Refreshed {ok}/{total} constituent lists.")
    # Non-zero only if nothing at all refreshed (the job's `|| echo` tolerates it).
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
