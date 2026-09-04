#!/usr/bin/env python
"""
refresh_screener.py
-------------------
Controlled DAILY market snapshot from public Screener.in pages (personal use).

- Fetches each unique NSE constituent ONCE (deduped across all sector universes).
- Computes bottom-up sector metrics in Python (core.screener; no AI arithmetic).
- Writes data/screener_snapshot.json (today's market snapshot) AND a dated copy
  under data/screener_history/<date>.json.
- Idempotent: if today's valid snapshot already exists, it does nothing unless
  --force is passed.
- Fail-soft: on total failure the previous snapshot is preserved; on partial
  failure, companies that failed reuse their previous values (marked stale) and
  successfully fetched companies are updated.

This is meant to run once/day (e.g. a scheduled GitHub Action). The Streamlit app
only READS the snapshot — it never scrapes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
from core import screener as SC  # noqa: E402
from core import sector_universe as U  # noqa: E402

LOGGER = logging.getLogger("fundacheck.refresh_screener")
SNAPSHOT = ROOT / "data" / "screener_snapshot.json"
HISTORY = ROOT / "data" / "screener_history"
SOURCE = "Screener.in (public company pages)"


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("sectors") else None
    except Exception:
        return None


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    import os
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _prev_company_index(prev: dict | None) -> dict[str, dict]:
    """Map nse_symbol -> previous per-company row (for partial-failure reuse)."""
    idx: dict[str, dict] = {}
    if not prev:
        return idx
    for s in prev.get("sectors", []):
        for r in s.get("constituents", []):
            sym = r.get("nse_symbol")
            if sym and sym not in idx:
                idx[sym] = r
    return idx


def run(*, sectors: list[str] | None = None, limit: int | None = None,
        force: bool = False, pacing: float = SC._PACING) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    today = dt.date.today().isoformat()
    prev = _load(SNAPSHOT)

    if prev and prev.get("market_snapshot_date") == today and not force:
        LOGGER.info("Today's Screener snapshot already exists (%s) — nothing to do.", today)
        return 0

    per_sector, union = U.all_unique_symbols()
    if sectors:
        per_sector = {k: v for k, v in per_sector.items() if k in sectors}
        union = list(dict.fromkeys(s for k in per_sector for s in per_sector[k]))
    if limit:
        union = union[:limit]
        keep = set(union)
        per_sector = {k: [s for s in v if s in keep] for k, v in per_sector.items()}

    LOGGER.info("Fetching %d unique companies from Screener (pacing %.1fs)...", len(union), pacing)
    sess = requests.Session()
    fetched: dict[str, SC.ScreenerCompany] = {}
    failed: list[str] = []
    for i, sym in enumerate(union, 1):
        c = SC.fetch(sym, sess)
        if c:
            fetched[sym] = c
        else:
            failed.append(sym)
        if i % 25 == 0:
            LOGGER.info("  ...%d/%d (%d ok, %d failed)", i, len(union), len(fetched), len(failed))
        time.sleep(pacing)

    if not fetched:
        LOGGER.error("Screener fetch produced ZERO companies — keeping previous snapshot.")
        return 3

    prev_rows = _prev_company_index(prev)
    prev_date = (prev or {}).get("market_snapshot_date")
    sectors_out = []
    for key in per_sector:
        uni = U.UNIVERSES[key]
        syms = per_sector.get(key, [])
        comps = [fetched[s] for s in syms if s in fetched]
        metrics = SC.pooled_metrics(comps, is_financial=uni.is_financial)
        constituents = SC.build_constituents(comps, is_financial=uni.is_financial)

        # partial failure: reuse previous row for constituents we could not fetch today
        have = {r["nse_symbol"] for r in constituents}
        stale = []
        for s in syms:
            if s not in have and s in prev_rows:
                row = dict(prev_rows[s])
                row["stale"] = True
                row["stale_since"] = row.get("stale_since") or prev_date
                constituents.append(row)
                stale.append(s)
        constituents.sort(key=lambda r: (r.get("market_cap") or 0), reverse=True)
        for i, r in enumerate(constituents, 1):
            r["rank"] = i

        periods = [fetched[s].fundamental_period for s in syms if s in fetched and fetched[s].fundamental_period]
        fund_period = max(set(periods), key=periods.count) if periods else None

        sectors_out.append({
            "key": key,
            "sector_name": uni.sector_name,
            "reference_index": uni.reference_index,
            "is_financial": uni.is_financial,
            "constituent_count": len(syms),
            "included_count": len(comps),
            "stale_count": len(stale),
            "stale_symbols": stale,
            "fundamental_period": fund_period,
            "metrics": metrics,
            "constituents": constituents,
        })

    now = dt.datetime.now(dt.timezone.utc)
    snapshot = {
        "generated_at": now.isoformat(),
        "market_snapshot_date": today,
        "source": SOURCE,
        "source_note": "Screener.in public data — not official exchange data, not guaranteed real-time.",
        "unique_companies": len(union),
        "fetched_ok": len(fetched),
        "fetch_failed": len(failed),
        "failed_symbols": failed,
        "sectors": sectors_out,
    }

    _atomic_write(SNAPSHOT, snapshot)
    _atomic_write(HISTORY / f"{today}.json", snapshot)
    LOGGER.info("Wrote %s: %d/%d companies, %d sectors.", SNAPSHOT, len(fetched), len(union), len(sectors_out))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily Screener market snapshot.")
    ap.add_argument("--sector", type=str, default=None, help="Comma-separated sector keys (testing).")
    ap.add_argument("--limit", type=int, default=None, help="Cap unique companies (testing).")
    ap.add_argument("--force", action="store_true", help="Refresh even if today's snapshot exists.")
    ap.add_argument("--pacing", type=float, default=SC._PACING, help="Delay between requests (s).")
    a = ap.parse_args()
    secs = [s.strip() for s in a.sector.split(",")] if a.sector else None
    return run(sectors=secs, limit=a.limit, force=a.force, pacing=a.pacing)


if __name__ == "__main__":
    raise SystemExit(main())
