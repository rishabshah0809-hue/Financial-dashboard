"""
tilt.py
-------
Two clearly separated things for the Sector Cycle & Seasonality section:

A. **Structural seasonality** — a relatively stable, qualitative description of
   how a sector typically behaves through the year. It is editorial context, not
   a statistical backtest, and is *not* presented as one. It changes only when
   an editor changes the text here.

B. **Current monthly tilt** — recalculated every monthly refresh from the real
   month-over-month deltas in the snapshot (earnings/revenue growth, operating
   performance, ROE/ROA, valuation). The classification and its one-line
   explanation are derived from the numbers; when the data does not support a
   read, the tilt is ``Mixed`` / ``Stable`` rather than an invented story. The
   text is deterministic — the same deltas always produce the same words.
"""

from __future__ import annotations

# Possible current-tilt classifications (the only labels ever emitted).
TILTS = ("Expansion", "Recovery", "Stable", "Mid-cycle", "Slowdown", "Mixed")

# Structural (stable) seasonality per FundaCheck sector key. Qualitative only.
STRUCTURAL: dict[str, str] = {
    "banking": "Credit growth and deposit mobilisation tend to firm into the "
               "March financial year-end, with treasury income sensitive to the "
               "rate cycle. Asset-quality disclosures cluster around quarterly "
               "results.",
    "it_services": "Deal signings and guidance set the tone at the April "
                   "year-end and again in the January quarter; furloughs soften "
                   "the December quarter. Margins track the wage-hike cycle.",
    "fmcg": "Volume-led and defensive, with a festive-season lift into the "
            "October–December quarter and rural demand keyed to the monsoon. "
            "Input-cost swings move margins more than volumes.",
    "pharma": "Steady domestic formulations demand with a seasonal respiratory "
              "lift in winter; US-generic pricing and USFDA actions are the "
              "swing factors rather than the calendar.",
    "realestate": "Launches and bookings build into the festive and year-end "
                  "quarters; the cycle is driven far more by rates, approvals "
                  "and inventory than by any month of the year.",
    "infrastructure": "Execution and ordering are back-ended to the March "
                      "year-end, then slow through the monsoon. Government "
                      "capex and award activity dominate the pattern.",
    "manufacturing": "Broadly pro-cyclical, tied to the capex and inventory "
                     "cycle; auto-linked names see a festive lift, while metals "
                     "track global prices more than the season.",
    "retail": "Consumption is festive-led into the second half, with discretionary "
              "spend sensitive to inflation and wage growth. Store additions and "
              "same-store-sales set the medium-term trend.",
    "generic": "A diversified, large-cap mix whose behaviour reflects the broad "
               "market cycle rather than a single sector's seasonality.",
}


def _delta(new, old):
    """new − old when both are real numbers, else None."""
    if isinstance(new, (int, float)) and isinstance(old, (int, float)):
        return new - old
    return None


def current_tilt(sector_key: str, current: dict, previous: dict | None) -> dict:
    """Classify the current tilt from real deltas.

    ``current`` / ``previous`` are per-sector snapshot dicts (this month / last
    month). Without a valid previous month there is no delta to read, so the tilt
    is a data-supported ``Stable`` / ``Mixed`` with an explicit 'no prior
    snapshot' explanation — never a fabricated trend.
    """
    structural = STRUCTURAL.get(sector_key, STRUCTURAL["generic"])

    if not previous:
        return {
            "current_tilt": "Stable",
            "tilt_reason": "First snapshot — no previous month to compare, so no "
                           "directional tilt is asserted.",
            "structural_seasonality": structural,
            "signals": {},
        }

    cm, pm = current.get("metrics", {}), previous.get("metrics", {})
    # Growth signals prefer earnings growth, then revenue growth (pooled proxies
    # via the sector's own reported growth, else via ROE as an earnings-quality
    # proxy). Every signal is only used when both months carry a real value.
    signals = {
        "roe": _delta(cm.get("roe"), pm.get("roe")),
        "roa": _delta(cm.get("roa"), pm.get("roa")),
        "pe": _delta(cm.get("pe"), pm.get("pe")),
        "earnings_growth": _delta(current.get("earnings_growth"),
                                  previous.get("earnings_growth")),
        "revenue_growth": _delta(current.get("revenue_growth"),
                                 previous.get("revenue_growth")),
    }
    usable = {k: v for k, v in signals.items() if v is not None}
    if not usable:
        return {
            "current_tilt": "Mixed",
            "tilt_reason": "No comparable metrics between the two snapshots, so "
                           "the current tilt cannot be classified.",
            "structural_seasonality": structural,
            "signals": signals,
        }

    # Directional score: profitability/growth improving is positive, richening
    # valuation is a mild positive, deteriorating returns negative.
    up = sum(1 for k in ("roe", "roa", "earnings_growth", "revenue_growth")
             if signals.get(k) is not None and signals[k] > 0.05)
    down = sum(1 for k in ("roe", "roa", "earnings_growth", "revenue_growth")
               if signals.get(k) is not None and signals[k] < -0.05)

    if up and not down:
        tilt = "Expansion"
        reason = _reason("improved", signals)
    elif down and not up:
        tilt = "Slowdown"
        reason = _reason("moderated", signals)
    elif up and down:
        tilt = "Mixed"
        reason = "Signals diverge — some metrics improved while others softened "
        reason += "versus the previous snapshot."
    else:
        tilt = "Stable"
        reason = "Metrics are broadly unchanged versus the previous snapshot."

    return {
        "current_tilt": tilt,
        "tilt_reason": reason,
        "structural_seasonality": structural,
        "signals": {k: (round(v, 2) if isinstance(v, (int, float)) else v)
                    for k, v in signals.items()},
    }


def _reason(direction: str, signals: dict) -> str:
    """Build the one-line explanation from whichever real signals moved."""
    parts = []
    eg = signals.get("earnings_growth")
    rg = signals.get("revenue_growth")
    roe = signals.get("roe")
    if eg is not None and abs(eg) > 0.05:
        parts.append(f"earnings growth {direction} by {abs(eg):.1f} pts")
    if rg is not None and abs(rg) > 0.05:
        parts.append(f"revenue growth {direction} by {abs(rg):.1f} pts")
    if roe is not None and abs(roe) > 0.05:
        verb = "rose" if roe > 0 else "fell"
        parts.append(f"sector ROE {verb} {abs(roe):.1f} pts")
    if not parts:
        return f"Sector fundamentals {direction} versus the previous snapshot."
    return "Current tilt driven by: " + "; ".join(parts) + "."
