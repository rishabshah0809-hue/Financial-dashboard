"""
tilt.py
-------
Structural cycle & seasonality profiles combined with data-grounded current monthly tilt.

Two clearly separated elements:
A. Structural seasonality: Stable, rich qualitative description of how each of the
   9 FundaCheck sectors typically behaves across market cycles and throughout the year.
B. Current monthly tilt: Recalculated on every monthly snapshot generation from
   real month-over-month deltas (earnings growth, revenue growth, ROE, ROA, valuation,
   market cap, and Top 10 composition shifts).

Possible Current Tilt states (strictly one of these 6):
  1. Expansion
  2. Recovery
  3. Stable
  4. Mid-cycle
  5. Slowdown
  6. Mixed
"""

from __future__ import annotations

TILTS = ("Expansion", "Recovery", "Stable", "Mid-cycle", "Slowdown", "Mixed")

# Rich structural seasonality from FundaCheck rule books
SECTOR_CYCLE: dict[str, dict] = {
    "banking": {
        "nature": "Cyclical",
        "seasonal": True,
        "text": (
            "Banking & finance is a cyclical sector geared to the credit and interest-rate "
            "cycle. Loan growth, net interest margins and credit costs move with GDP and RBI "
            "policy: the sector expands as rates ease and credit demand rises, while "
            "asset-quality stress and provisioning tend to surface late in the cycle after "
            "tightening. There is a clear intra-year rhythm — disbursements build through H2 "
            "(Oct–Mar) around the festive season and fiscal year-end, and Q4 (Jan–Mar) is "
            "usually the strongest quarter."
        ),
    },
    "it_services": {
        "nature": "Cyclical (global)",
        "seasonal": True,
        "text": (
            "IT services is a structural-growth sector but cyclical to global (mainly US/Europe) "
            "technology budgets and the USD/INR rate. Deal signings and discretionary spend "
            "soften in global slowdowns and re-accelerate in recoveries. Seasonality is mild but "
            "consistent: the first half of the fiscal year (Apr–Sep) is seasonally strong on "
            "budget flush and project ramp-ups, while Q3 (Oct–Dec) is softer due to furloughs "
            "and holidays. A weaker rupee is a margin tailwind."
        ),
    },
    "fmcg": {
        "nature": "Defensive",
        "seasonal": True,
        "text": (
            "FMCG & consumer staples is a defensive, low-cyclicality sector — demand for "
            "everyday essentials holds up across the economic cycle. What moves it is "
            "seasonal and input-cost driven: rural demand tracks the monsoon and harvest, the "
            "festive second half (Q3) lifts volumes, and summer supports beverages. Margins swing "
            "more with commodity cycles (palm oil, crude derivatives, grains) than with "
            "demand, so the sector is a relative safe-haven in downturns."
        ),
    },
    "pharma": {
        "nature": "Defensive",
        "seasonal": True,
        "text": (
            "Pharmaceuticals & healthcare is largely defensive — domestic drug demand is "
            "inelastic to the economic cycle. The export/US-generics business carries its own "
            "pricing cycle (price erosion vs new launches) and is sensitive to USFDA regulatory "
            "events, which drive company-specific swings more than macro. Seasonality is modest: "
            "acute therapies such as anti-infectives and respiratory peak in the monsoon and "
            "winter (roughly Q2–Q3)."
        ),
    },
    "realestate": {
        "nature": "Highly cyclical",
        "seasonal": True,
        "text": (
            "Real estate is highly cyclical and rate-sensitive, moving in multi-year up- and "
            "down-cycles driven by affordability, interest rates and inventory overhang. Long "
            "cycles dominate, but there is seasonality too: launches and bookings cluster "
            "around the festive season (Q3), and registrations often rise near the fiscal year-end. "
            "Falling rates and low unsold inventory mark the up-phase; rising rates and rising "
            "inventory mark the down-phase."
        ),
    },
    "infrastructure": {
        "nature": "Highly cyclical",
        "seasonal": True,
        "text": (
            "Infrastructure, power & capital goods is highly cyclical and capex-led. Order "
            "inflows track the government and private capital-expenditure cycle and are "
            "sensitive to interest rates because projects are long-gestation and debt-funded. "
            "Execution has a strong seasonal pattern: activity slows through the monsoon (Q2, "
            "Jul–Sep) and accelerates into H2, peaking in the Jan–Mar fiscal year-end push. "
            "Leverage makes the sector an early beneficiary of easing cycles and an early "
            "casualty of tightening ones."
        ),
    },
    "manufacturing": {
        "nature": "Cyclical",
        "seasonal": True,
        "text": (
            "Manufacturing & industrials is cyclical, geared to industrial activity and "
            "commodity cycles. Volumes rise with the broader economy and compress in slowdowns, "
            "while margins swing with input (metals, energy) prices. Consumer-facing lines such "
            "as autos and durables show festive seasonality — Q3 (Oct–Dec) is typically the "
            "strongest quarter — and demand softens through the monsoon. Best judged across a "
            "full cycle rather than a single year."
        ),
    },
    "retail": {
        "nature": "Cyclical (discretionary)",
        "seasonal": True,
        "text": (
            "Retail & consumption is cyclical, tied to discretionary spending and therefore to "
            "inflation, rates and consumer confidence. Seasonality is pronounced: the festive Q3 "
            "(Oct–Dec, Diwali) is the peak quarter, the wedding season supports Q4, and "
            "Q1/monsoon months are softer. The model runs on velocity, so footfalls and "
            "same-store growth lead the cycle; margins are thin and sensitive to discounting "
            "during weak demand."
        ),
    },
    "generic": {
        "nature": "Blended",
        "seasonal": True,
        "text": (
            "A diversified basket broadly tracks the overall market and GDP cycle, so its "
            "cyclicality depends on the underlying mix of businesses. Indian equities in "
            "aggregate show the familiar seasonal pattern — a stronger festive H2 and a fiscal "
            "year-end (Jan–Mar) push — layered on top of the macro cycle. Treat this profile as a "
            "market-level reference rather than a single-sector read."
        ),
    },
}

STRUCTURAL = {k: v["text"] for k, v in SECTOR_CYCLE.items()}


def profile(sector_key: str) -> dict | None:
    """Return structural cycle profile for a sector."""
    return SECTOR_CYCLE.get(sector_key, SECTOR_CYCLE.get("generic"))


def _delta(new, old) -> float | None:
    if isinstance(new, (int, float)) and isinstance(old, (int, float)):
        return round(new - old, 2)
    return None


def current_tilt(sector_key: str, current: dict, previous: dict | None) -> dict:
    """Classify the current monthly tilt from real month-over-month data.

    Returns a dict with:
      - current_tilt: one of ('Expansion', 'Recovery', 'Stable', 'Mid-cycle', 'Slowdown', 'Mixed')
      - tilt_reason: concise data-driven explanation
      - structural_seasonality: rich text description
      - nature: sector character label
      - seasonal: boolean
      - signals: dict of computed deltas
    """
    prof = profile(sector_key) or SECTOR_CYCLE["generic"]
    structural = prof["text"]

    if not previous:
        # Grounded initial classification when no prior month is available
        eg = current.get("earnings_growth")
        if isinstance(eg, (int, float)):
            if eg >= 12.0:
                tilt = "Expansion"
                reason = f"Initial snapshot: strong pooled earnings growth (+{eg:.1f}%) indicates expansionary momentum."
            elif eg > 0.0:
                tilt = "Mid-cycle"
                reason = f"Initial snapshot: moderate positive earnings growth (+{eg:.1f}%) reflects steady mid-cycle activity."
            else:
                tilt = "Slowdown"
                reason = f"Initial snapshot: contracting earnings growth ({eg:.1f}%) points to late-cycle / slowdown pressure."
        else:
            tilt = "Stable"
            reason = "First snapshot — baseline established. Directional month-over-month tilt will compute upon next refresh."

        return {
            "current_tilt": tilt,
            "tilt_reason": reason,
            "structural_seasonality": structural,
            "nature": prof["nature"],
            "seasonal": prof["seasonal"],
            "signals": {},
        }

    cm = current.get("metrics", {})
    pm = previous.get("metrics", {})

    signals = {
        "roe": _delta(cm.get("roe"), pm.get("roe")),
        "roa": _delta(cm.get("roa"), pm.get("roa")),
        "pe": _delta(cm.get("pe"), pm.get("pe")),
        "earnings_growth": _delta(current.get("earnings_growth"), previous.get("earnings_growth")),
        "revenue_growth": _delta(current.get("revenue_growth"), previous.get("revenue_growth")),
    }

    # Top 10 shifts
    cur_top = {r.get("nse_symbol") for r in current.get("top10", []) if r.get("nse_symbol")}
    prev_top = {r.get("nse_symbol") for r in previous.get("top10", []) if r.get("nse_symbol")}
    top10_changed = len(cur_top - prev_top) if cur_top and prev_top else 0
    signals["top10_changed"] = top10_changed

    usable = {k: v for k, v in signals.items() if v is not None and k != "top10_changed"}
    if not usable:
        return {
            "current_tilt": "Stable",
            "tilt_reason": "No metric deltas available between snapshots; fundamentals remain stable.",
            "structural_seasonality": structural,
            "nature": prof["nature"],
            "seasonal": prof["seasonal"],
            "signals": signals,
        }

    up_count = sum(1 for k in ("roe", "roa", "earnings_growth", "revenue_growth")
                   if signals.get(k) is not None and signals[k] > 0.10)
    down_count = sum(1 for k in ("roe", "roa", "earnings_growth", "revenue_growth")
                     if signals.get(k) is not None and signals[k] < -0.10)

    # Classify state
    if up_count >= 2 and down_count == 0:
        tilt = "Expansion"
        reason = _format_reason("expanded", signals)
    elif up_count > 0 and down_count == 0:
        tilt = "Recovery"
        reason = _format_reason("recovering", signals)
    elif down_count >= 2 and up_count == 0:
        tilt = "Slowdown"
        reason = _format_reason("softening", signals)
    elif up_count > 0 and down_count > 0:
        tilt = "Mixed"
        reason = "Mixed signals: some sector metrics improved while others moderated versus the previous month."
    else:
        # Check if growth is positive
        cur_eg = current.get("earnings_growth")
        if isinstance(cur_eg, (int, float)) and cur_eg > 0:
            tilt = "Mid-cycle"
            reason = f"Metrics are steady with positive earnings growth ({cur_eg:+.1f}%), consistent with mid-cycle dynamics."
        else:
            tilt = "Stable"
            reason = "Sector fundamentals are broadly unchanged versus the previous month."

    return {
        "current_tilt": tilt,
        "tilt_reason": reason,
        "structural_seasonality": structural,
        "nature": prof["nature"],
        "seasonal": prof["seasonal"],
        "signals": signals,
    }


def _format_reason(verb: str, signals: dict) -> str:
    parts = []
    eg = signals.get("earnings_growth")
    rg = signals.get("revenue_growth")
    roe = signals.get("roe")
    if eg is not None and abs(eg) > 0.05:
        parts.append(f"earnings growth delta {eg:+.1f} pts")
    if rg is not None and abs(rg) > 0.05:
        parts.append(f"revenue growth delta {rg:+.1f} pts")
    if roe is not None and abs(roe) > 0.05:
        parts.append(f"sector ROE {roe:+.1f} pts")

    if parts:
        return f"Current tilt ({verb}) driven by: " + ", ".join(parts) + "."
    return f"Sector fundamentals {verb} compared to the previous snapshot."

