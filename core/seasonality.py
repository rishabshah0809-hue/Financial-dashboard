"""
seasonality.py
--------------
Historical / structural seasonality for the Sector Lens — kept strictly separate
from the *current* market cycle (that lives in ``core.market_context``).

Two modes, resolved automatically:

* **quantitative** — when enough real history has accumulated in
  ``data/screener_history/`` to compute a sector's month-by-month tendency, we
  do so from the actual data: for each daily snapshot we sum the sector's
  constituent market caps into a sector "level", resample to month-ends, take
  month-over-month returns, and average them by calendar month. This needs a
  couple of years of data before it is meaningful, so it stays dormant until the
  history is deep enough (never shown on a thin sample).

* **qualitative** — until then, a labelled qualitative strip built ONLY from the
  sector's known structural pattern (the peak/slow bands in ``core.tilt``). No
  invented numbers: the cells carry a state word, not a percentage.

No AI is used here — this is pure Python/data (see project rule §14).
"""

from __future__ import annotations

import json
from collections import OrderedDict, defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path

from . import tilt as TILT

HISTORY_DIR = Path(__file__).resolve().parent.parent / "data" / "screener_history"
MARKET_FILE = Path(__file__).resolve().parent.parent / "data" / "seasonality_history.json"
CALENDAR_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

FISCAL_MONTHS = TILT.FISCAL_MONTHS  # Apr..Mar

# Thresholds before the quantitative path is allowed to show — a seasonality
# read on a thin sample would be noise dressed as signal.
_MIN_YEARS = 2          # at least two fiscal years of coverage
_MIN_MONTHS = 20        # at least 20 month-end observations overall

# Qualitative state vocabulary (no numbers attached in this mode).
STATES = ("Strong", "Positive", "Neutral", "Soft", "Weak")
_STATE_COLOUR = {
    "Strong": "#1b7f4f", "Positive": "#57ab7d", "Neutral": "#c7d3cc",
    "Soft": "#e6c07a", "Weak": "#d09a8f",
}


# ---------------------------------------------------------------------------
# qualitative (structural) — derived from the tilt peak/slow bands
# ---------------------------------------------------------------------------
def qualitative(sector_key: str | None) -> dict:
    """A 12-cell Apr..Mar qualitative tendency strip from the sector's structural
    peak/slow bands. Returns states only — never a fabricated number."""
    shape = TILT.seasonal(sector_key)
    peak = set()
    slow = set()
    peak_label = slow_label = ""
    if shape:
        if shape.get("peak"):
            s, e, peak_label = shape["peak"]
            peak = set(range(s, e + 1))
        if shape.get("slow"):
            s, e, slow_label = shape["slow"]
            slow = set(range(s, e + 1))

    # Use the illustrative activity curve ONLY to rank months into qualitative
    # bands (never shown as a value). Months inside the named peak/slow bands are
    # pinned to the strong/weak ends so the strip matches the written structural
    # text; the remainder are graded relative to the sector's own range.
    act = (shape or {}).get("activity") or [50] * 12
    lo, hi = min(act), max(act)
    span = (hi - lo) or 1
    flat = not (peak or slow)
    cells = []
    for i, mon in enumerate(FISCAL_MONTHS):
        if i in peak:
            state = "Strong"
        elif i in slow:
            state = "Weak"
        elif flat:
            # No pronounced intra-year pattern (commodity/defensive): don't grade
            # noise into Soft/Positive — read Neutral honestly.
            state = "Neutral"
        else:
            r = (act[i] - lo) / span
            state = ("Positive" if r >= 0.66 else "Soft" if r <= 0.33 else "Neutral")
        cells.append({"month": mon, "state": state, "colour": _STATE_COLOUR[state]})

    note_bits = []
    if peak_label:
        note_bits.append(peak_label)
    if slow_label:
        note_bits.append(slow_label)
    return {
        "mode": "qualitative",
        "cells": cells,
        "peak_label": peak_label,
        "slow_label": slow_label,
        "methodology": (("Low intra-year seasonality — this sector is driven by "
                         "the commodity/price cycle rather than the calendar.")
                        if flat else
                        ("Qualitative structural tendency from the sector's known "
                         "operating pattern — not a price backtest.")),
        "flat": flat,
    }


# ---------------------------------------------------------------------------
# quantitative — real month-by-month returns once history is deep enough
# ---------------------------------------------------------------------------
def _sector_level(day_snap: dict, sector_key: str) -> float | None:
    """Sum of a sector's constituent market caps on one day = the sector level."""
    secs = day_snap.get("sectors")
    sec = None
    if isinstance(secs, dict):
        sec = secs.get(sector_key)
    elif isinstance(secs, list):
        sec = next((s for s in secs if s.get("key") == sector_key), None)
    if not sec:
        return None
    total = 0.0
    seen = False
    for r in sec.get("constituents", []):
        mc = r.get("market_cap")
        if isinstance(mc, (int, float)) and mc > 0:
            total += mc
            seen = True
    return total if seen else None


def _load_history() -> list[tuple[date, dict]]:
    if not HISTORY_DIR.exists():
        return []
    out = []
    for p in sorted(HISTORY_DIR.glob("*.json")):
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        try:
            out.append((d, json.loads(p.read_text(encoding="utf-8"))))
        except (ValueError, OSError):
            continue
    return out


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _sector_series(sector_key: str) -> list[tuple[date, float]]:
    """(date, sector level) for every daily snapshot that carries this sector."""
    out = []
    for d, snap in _load_history():
        lvl = _sector_level(snap, sector_key)
        if lvl is not None:
            out.append((d, lvl))
    return out


# ---------------------------------------------------------------------------
# market heatmap — real historical index price seasonality (IndianAPI history,
# stored by scripts/refresh_seasonality.py; the app only reads + computes here)
# ---------------------------------------------------------------------------
_MARKET_CACHE: dict | None = None


def _load_market() -> dict:
    """The stored index-history file (loaded once per process). Never fetches."""
    global _MARKET_CACHE
    if _MARKET_CACHE is None:
        try:
            _MARKET_CACHE = json.loads(MARKET_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _MARKET_CACHE = {}
    return _MARKET_CACHE


@lru_cache(maxsize=64)
def market_heatmap(sector_key: str | None) -> dict:
    """Year × calendar-month seasonality for a sector's reference index, computed
    from the stored real IndianAPI price history. All arithmetic is Python.

    Monthly return  = (last close of month ÷ first close of month − 1) × 100
    Yearly return   = (last close of year  ÷ first close of year  − 1) × 100
    Average row     = mean of each calendar month's returns across the years.

    Missing months stay None ("—", never fabricated). The current incomplete year
    is marked YTD and only carries months that actually have data. Returns a
    permanent status dict; ``sufficient`` gates the heatmap vs the building state.
    """
    entry = (_load_market().get("indices") or {}).get(sector_key or "")
    base: dict = {"mode": "market", "min_months": _MIN_MONTHS, "min_years": _MIN_YEARS,
                  "index": (entry or {}).get("index"),
                  "reconstructed": bool((entry or {}).get("reconstructed"))}

    pts: list[tuple[date, float]] = []
    for row in (entry or {}).get("closes") or []:
        try:
            pts.append((date.fromisoformat(str(row[0])), float(row[1])))
        except (ValueError, TypeError, IndexError):
            continue
    pts.sort()
    if not pts:
        base.update({"sufficient": False, "observations": 0, "days": 0,
                     "earliest": None, "latest": None})
        return base

    by_ym: dict[tuple[int, int], list[float]] = OrderedDict()
    by_year: dict[int, list[float]] = OrderedDict()
    for d, c in pts:
        by_ym.setdefault((d.year, d.month), []).append(c)   # already date-sorted
        by_year.setdefault(d.year, []).append(c)

    monthly: dict[tuple[int, int], float] = {}
    for k, vals in by_ym.items():
        first_c, last_c = vals[0], vals[-1]
        if first_c > 0:
            monthly[k] = (last_c / first_c - 1) * 100.0
    yearly: dict[int, float] = {}
    for y, vals in by_year.items():
        if vals[0] > 0:
            yearly[y] = (vals[-1] / vals[0] - 1) * 100.0

    years = sorted(by_year)
    observations = len(monthly)
    sufficient = observations >= _MIN_MONTHS and len(years) >= _MIN_YEARS
    base.update({"sufficient": sufficient, "observations": observations, "days": len(pts),
                 "earliest": pts[0][0].isoformat(), "latest": pts[-1][0].isoformat()})
    if not sufficient:
        return base

    latest_year, latest_month = pts[-1][0].year, pts[-1][0].month
    avg: list[float | None] = []
    for m in range(1, 13):
        col = [v for (yy, mm), v in monthly.items() if mm == m]   # real obs only
        avg.append(round(sum(col) / len(col), 2) if col else None)

    rows: list[dict] = []
    for y in sorted(years, reverse=True):
        cells = [round(monthly[(y, m)], 2) if (y, m) in monthly else None
                 for m in range(1, 13)]
        rows.append({"year": y,
                     "cells": cells,
                     "yearly": round(yearly[y], 2) if y in yearly else None,
                     "ytd": (y == latest_year and latest_month < 12)})

    base.update({
        "months": CALENDAR_MONTHS,
        "avg": avg,
        "rows": rows,
        "window": f"{years[0]}–{years[-1]}",
        "methodology": (
            f"Monthly return = (last close ÷ first close − 1) of each month for "
            f"{base['index']}; average row = mean of each month across {len(years)} years. "
            f"Real IndianAPI index history — all returns computed in Python."),
    })
    return base


def quantitative(sector_key: str | None) -> dict:
    """Historical MARKET seasonality from accumulated daily Screener snapshots.

    ALWAYS returns a status dict (the section is permanent, per rule §7):
      * ``sufficient=False`` while the dataset is still building — carries the
        observation count and the earliest/latest dates, and NO fabricated
        percentages (rule §3).
      * ``sufficient=True`` once the configurable threshold is met — carries
        per-calendar-month average & median return, positive-month frequency and
        the observation count. All arithmetic is Python; no AI (rule §8).
    """
    series = _sector_series(sector_key or "")
    days = len(series)
    earliest = series[0][0].isoformat() if series else None
    latest = series[-1][0].isoformat() if series else None

    # last level in each calendar month = that month's observation
    month_end: dict[tuple[int, int], float] = {}
    for d, lvl in series:
        month_end[(d.year, d.month)] = lvl
    keys = sorted(month_end)
    observations = len(keys)
    years = sorted({y for y, _ in keys})
    sufficient = observations >= _MIN_MONTHS and len(years) >= _MIN_YEARS

    status = {
        "mode": "quantitative",
        "sufficient": sufficient,
        "observations": observations,
        "days": days,
        "earliest": earliest,
        "latest": latest,
        "min_months": _MIN_MONTHS,
        "min_years": _MIN_YEARS,
    }
    if not sufficient:
        status["methodology"] = (
            "Building historical dataset from daily Screener snapshots — "
            "insufficient observations for a reliable backtest.")
        return status

    # month-over-month returns grouped by calendar month
    by_cal: dict[int, list[float]] = defaultdict(list)
    for i in range(1, len(keys)):
        prev_lvl, cur_lvl = month_end[keys[i - 1]], month_end[keys[i]]
        if prev_lvl and prev_lvl > 0:
            by_cal[keys[i][1]].append((cur_lvl / prev_lvl - 1) * 100.0)

    fiscal_order = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
    cells = []
    for m, mon in zip(fiscal_order, FISCAL_MONTHS):
        vals = by_cal.get(m, [])
        if not vals:
            cells.append({"month": mon, "value": None, "median": None,
                          "pos_freq": None, "n": 0, "state": "Neutral",
                          "colour": _STATE_COLOUR["Neutral"]})
            continue
        avg = sum(vals) / len(vals)
        med = _median(vals)
        pos = sum(1 for v in vals if v > 0) / len(vals)
        state = ("Strong" if avg >= 2 else "Positive" if avg >= 0.5
                 else "Weak" if avg <= -2 else "Soft" if avg <= -0.5 else "Neutral")
        cells.append({"month": mon, "value": round(avg, 2), "median": round(med, 2),
                      "pos_freq": round(pos * 100), "n": len(vals), "state": state,
                      "colour": _STATE_COLOUR[state]})

    status["cells"] = cells
    status["window"] = f"{years[0]}–{years[-1]}"
    status["methodology"] = (
        f"Average & median month-over-month sector-level change by calendar month, "
        f"based on {observations} monthly observations ({years[0]}–{years[-1]}) "
        f"from FundaCheck's daily Screener history.")
    return status
