"""
refresh_seasonality.py
----------------------
Populate data/seasonality_history.json with real historical index price series
for every niche sector's reference NIFTY index, from IndianAPI's /historical_data
endpoint (filter=price). The Streamlit app only READS this file and computes the
monthly/yearly seasonality heatmap in Python — it never calls IndianAPI at
runtime (project convention: app reads snapshots, scripts fetch).

Stored shape:
  { "generated_at": ISO,
    "indices": { "<sector_key>": {"index": "NIFTY IT", "closes": [["YYYY-MM-DD", close], ...]} } }

Only real API data is stored. Indices IndianAPI cannot serve are skipped (that
sector then shows the app's existing insufficient-data fallback). No mock, no
fabricated values.

Run:  INDIANAPI_KEY=... python scripts/refresh_seasonality.py [--sector it,bank] [--pacing 0.6]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import sector_universe as U  # noqa: E402

LOGGER = logging.getLogger("fundacheck.seasonality")
OUT = Path(__file__).resolve().parent.parent / "data" / "seasonality_history.json"
BASE = os.environ.get("INDIANAPI_BASE_URL", "https://stock.indianapi.in").rstrip("/")


def _key() -> str:
    for var in ("INDIANAPI_KEY", "INDIAN_STOCK_MARKET_API_KEY", "INDIAN_API_KEY"):
        v = (os.environ.get(var) or "").strip()
        if v:
            return v
    return ""


def fetch_index_closes(sess: requests.Session, key: str, index_name: str) -> list[list] | None:
    """Return [["YYYY-MM-DD", close_float], ...] for an index, or None."""
    for name in (index_name, index_name.title()):
        try:
            r = sess.get(f"{BASE}/historical_data", headers={"X-Api-Key": key},
                         params={"stock_name": name, "period": "max", "filter": "price"},
                         timeout=40)
        except requests.RequestException as e:
            LOGGER.warning("network error for %s: %s", index_name, type(e).__name__)
            return None
        if r.status_code in (401, 403, 429):
            LOGGER.error("auth/limit HTTP %s — stopping", r.status_code)
            raise SystemExit(2)
        if r.status_code != 200:
            continue
        try:
            datasets = r.json().get("datasets") or []
        except ValueError:
            continue
        price = next((d for d in datasets if str(d.get("metric", "")).lower() == "price"), None)
        vals = (price or {}).get("values") or []
        out: list[list] = []
        for row in vals:
            try:
                d = dt.date.fromisoformat(str(row[0])).isoformat()
                c = float(row[1])
            except (ValueError, TypeError, IndexError):
                continue
            if c > 0:
                out.append([d, c])
        if len(out) >= 12:            # a usable series
            out.sort()
            return out
    return None


def run(sectors: list[str] | None = None, pacing: float = 0.6) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    key = _key()
    if not key:
        LOGGER.error("No IndianAPI key in env (INDIANAPI_KEY). Aborting.")
        return 1
    keys = list(sectors) if sectors else list(U.ORDER)
    sess = requests.Session()
    indices: dict = {}
    for i, sk in enumerate(keys, 1):
        uni = U.UNIVERSES.get(sk)
        if not uni:
            continue
        idx = getattr(uni, "reference_index", "") or getattr(uni, "index_label", "")
        if not idx:
            continue
        closes = fetch_index_closes(sess, key, idx)
        if closes:
            indices[sk] = {"index": idx, "closes": closes}
            LOGGER.info("[%d/%d] %s (%s): %d closes %s→%s",
                        i, len(keys), sk, idx, len(closes), closes[0][0], closes[-1][0])
        else:
            LOGGER.info("[%d/%d] %s (%s): no historical data", i, len(keys), sk, idx)
        time.sleep(pacing)

    if not indices:
        LOGGER.error("Fetched ZERO index series — keeping any existing file.")
        return 3
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
               "source": "IndianAPI /historical_data (filter=price)",
               "indices": indices}
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(OUT)
    LOGGER.info("Wrote %s: %d indices.", OUT, len(indices))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh historical index seasonality data.")
    ap.add_argument("--sector", type=str, default=None, help="Comma-separated sector keys.")
    ap.add_argument("--pacing", type=float, default=0.6)
    a = ap.parse_args()
    secs = [s.strip() for s in a.sector.split(",")] if a.sector else None
    return run(sectors=secs, pacing=a.pacing)


if __name__ == "__main__":
    raise SystemExit(main())
