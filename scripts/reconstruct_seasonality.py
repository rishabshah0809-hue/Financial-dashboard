"""
reconstruct_seasonality.py
--------------------------
Build a sector's historical monthly-seasonality series by RECONSTRUCTING an
equal-weighted basket from its real constituent stocks, when the official NIFTY
sub-index history is not available from IndianAPI.

Source: Yahoo Finance chart API (public, no key) — weekly closes per constituent
``<SYMBOL>.NS``. We do NOT claim this is the official index: each stored entry is
flagged ``"reconstructed": true`` and the app labels the heatmap accordingly.

Method (honest, no fabricated values):
  * pull each constituent's weekly close history (default ~6y),
  * rebase each stock to its own first close in the window (=1.0),
  * the basket level each week = 100 x mean of the available rebased ratios
    (equal weight; weeks with fewer than --min-names present are dropped),
  * store as ``closes: [["YYYY-MM-DD", level], ...]`` — the SAME shape the app's
    core.seasonality.market_heatmap already reads, so the monthly/yearly returns
    are computed there exactly as for the IndianAPI-sourced indices.

Writes/merges into data/seasonality_history.json (never overwrites other sectors).

Run:
  python scripts/reconstruct_seasonality.py                     # the 14 missing sectors
  python scripts/reconstruct_seasonality.py --sector cement,power
  python scripts/reconstruct_seasonality.py --all               # every sector
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import sector_universe as U  # noqa: E402

LOGGER = logging.getLogger("fundacheck.reconstruct")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "seasonality_history.json"
CONSTITUENTS = ROOT / "data" / "nse_constituents"
YF = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (FundaCheck seasonality reconstruction)"}

# Sectors whose official index history IndianAPI does not serve (as of build).
DEFAULT_MISSING = [
    "nbfc", "housing_finance", "insurance", "telecom", "hospitals",
    "consumer_services", "retail", "cement", "chemicals", "power",
    "capital_goods", "construction", "commercial_transport", "commodities",
]


def _symbols(sector_key: str) -> list[str]:
    p = CONSTITUENTS / f"{sector_key}.csv"
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sym = (row.get("Symbol") or "").strip()
            if sym:
                out.append(sym)
    return out


def _yahoo_weekly(sym: str, rng: str, cache: dict) -> dict[str, float] | None:
    """{ 'YYYY-MM-DD': close } of weekly closes for <sym>.NS, or None."""
    tkr = f"{sym}.NS"
    if tkr in cache:
        return cache[tkr]
    try:
        r = requests.get(YF.format(sym=tkr), headers=_HEADERS,
                         params={"range": rng, "interval": "1wk"}, timeout=25)
        j = r.json()
    except (requests.RequestException, ValueError):
        cache[tkr] = None
        return None
    res = (j.get("chart") or {}).get("result")
    if not res:
        cache[tkr] = None
        return None
    ts = res[0].get("timestamp") or []
    closes = (((res[0].get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    series: dict[str, float] = {}
    for t, c in zip(ts, closes):
        if c is None or c <= 0:
            continue
        d = dt.date.fromtimestamp(t).isoformat()
        series[d] = float(c)
    cache[tkr] = series or None
    return cache[tkr]


def reconstruct(sector_key: str, rng: str, min_names: int, pacing: float,
                cache: dict) -> dict | None:
    syms = _symbols(sector_key)
    if not syms:
        LOGGER.warning("%s: no constituent CSV", sector_key)
        return None
    per: list[dict[str, float]] = []
    for s in syms:
        wk = _yahoo_weekly(s, rng, cache)
        if wk:
            per.append(wk)
        time.sleep(pacing)
    if len(per) < min_names:
        LOGGER.info("%s: only %d/%d constituents returned data (<%d) — skipped",
                    sector_key, len(per), len(syms), min_names)
        return None

    # rebase each stock to its own first close in the window
    bases = [min(wk.items(), key=lambda kv: kv[0])[1] for wk in per]
    dates = sorted({d for wk in per for d in wk})
    closes: list[list] = []
    for d in dates:
        ratios = [wk[d] / b for wk, b in zip(per, bases) if d in wk and b]
        if len(ratios) >= min_names:
            closes.append([d, round(100.0 * sum(ratios) / len(ratios), 4)])
    if len(closes) < 24:
        LOGGER.info("%s: only %d weekly points — skipped", sector_key, len(closes))
        return None

    uni = U.UNIVERSES.get(sector_key)
    idx = getattr(uni, "reference_index", "") or getattr(uni, "index_label", "") or sector_key
    LOGGER.info("%s (%s): reconstructed %d weeks from %d/%d constituents %s→%s",
                sector_key, idx, len(closes), len(per), len(syms),
                closes[0][0], closes[-1][0])
    return {"index": idx, "closes": closes, "reconstructed": True,
            "constituents_used": len(per), "constituents_total": len(syms),
            "method": "equal-weight basket of constituents (Yahoo Finance weekly closes)"}


def run(sectors: list[str], rng: str, min_names: int, pacing: float) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cache: dict = {}
    built: dict = {}
    for i, sk in enumerate(sectors, 1):
        LOGGER.info("[%d/%d] %s …", i, len(sectors), sk)
        entry = reconstruct(sk, rng, min_names, pacing, cache)
        if entry:
            built[sk] = entry
    if not built:
        LOGGER.error("Reconstructed ZERO sectors — keeping existing file.")
        return 3
    merged: dict = {}
    if OUT.exists():
        try:
            merged = (json.loads(OUT.read_text(encoding="utf-8")).get("indices") or {})
        except (ValueError, OSError):
            merged = {}
    merged.update(built)
    payload = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
               "source": "IndianAPI /historical_data + reconstructed baskets (Yahoo Finance)",
               "indices": merged}
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(OUT)
    LOGGER.info("Wrote %s: %d indices (%d reconstructed this run).",
                OUT, len(merged), len(built))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Reconstruct sector seasonality from constituents.")
    ap.add_argument("--sector", type=str, default=None, help="Comma-separated sector keys.")
    ap.add_argument("--all", action="store_true", help="Every sector in the universe.")
    ap.add_argument("--range", type=str, default="6y", help="Yahoo range (default 6y).")
    ap.add_argument("--min-names", type=int, default=3, help="Min constituents present per week.")
    ap.add_argument("--pacing", type=float, default=0.35)
    a = ap.parse_args()
    if a.all:
        secs = list(U.ORDER)
    elif a.sector:
        secs = [s.strip() for s in a.sector.split(",") if s.strip()]
    else:
        secs = list(DEFAULT_MISSING)
    return run(secs, a.range, a.min_names, a.pacing)


if __name__ == "__main__":
    raise SystemExit(main())
