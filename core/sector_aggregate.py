"""
sector_aggregate.py
-------------------
Pooled (aggregate) sector metrics from a set of constituent Company records.

Methodology — pooled, never a simple average of company ratios:

    P/E  = Σ MarketCap / Σ NetIncome     (loss-makers, NetIncome<=0, excluded
                                           from BOTH sums — the NSE index-PE rule)
    P/B  = Σ MarketCap / Σ Equity
    ROE  = Σ NetIncome / Σ Equity                          (× 100, as %)
    ROA  = Σ NetIncome / Σ TotalAssets                     (× 100, as %)
    ROCE = Σ EBIT      / Σ CapitalEmployed                 (× 100, as %)
           EBIT           = EBITDA − D&A (same fiscal period; else the company
                            is skipped for ROCE)
           CapitalEmployed = Total Equity + Total Debt
           ROCE is only computed for sectors where it is applicable — for
           Banking & Finance / NBFCs it is withheld ("—") with a reason.

A company enters a metric only when every input that metric needs is present and
valid; otherwise it is skipped for that metric and the reason is recorded. A
value is never invented. Pooled results are bounds-checked; a figure outside a
plausible range (a sign of a unit mismatch in the source) is withheld rather
than shown, with the reason recorded.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .indianapi import Company
from .sector_universe import METRICS, SectorUniverse, metric_applicable

# Plausible ranges; a pooled result outside these is withheld (not shown wrong).
_BOUNDS = {"pe": (2.0, 300.0), "pb": (0.1, 60.0),
           "roe": (-100.0, 150.0), "roce": (-100.0, 150.0), "roa": (-50.0, 80.0)}

SOURCE_LABEL = "IndianAPI (company fundamentals) + NSE Indices (constituents)"
METHODOLOGY = ("Pooled aggregate (Σnumerator / Σdenominator), not a simple average "
               "of company ratios. P/E excludes loss-makers. "
               "EBIT = EBITDA − D&A (same period); Capital Employed = Equity + Total Debt.")


def _metric(companies, num_fn, den_fn, *, den_positive, num_positive=False,
            as_percent=False, bound_key=None, den_skip="non-positive denominator"):
    """Pooled num/den with per-company inclusion + skip accounting."""
    included, skipped, num_sum, den_sum = [], [], 0.0, 0.0
    for c in companies:
        num, den = num_fn(c), den_fn(c)
        if num is None:
            skipped.append((c.symbol, "missing numerator")); continue
        if den is None:
            skipped.append((c.symbol, "missing denominator")); continue
        if num_positive and num <= 0:
            skipped.append((c.symbol, "non-positive numerator")); continue
        if den_positive and den <= 0:
            skipped.append((c.symbol, den_skip)); continue
        num_sum += num; den_sum += den; included.append(c.symbol)
    if not included or den_sum == 0:
        return {"value": None, "included": included, "skipped": skipped,
                "note": "insufficient constituent data"}
    val = num_sum / den_sum * (100.0 if as_percent else 1.0)
    note = ""
    if bound_key:
        lo, hi = _BOUNDS[bound_key]
        if not (lo <= val <= hi):
            return {"value": None, "included": included, "skipped": skipped,
                    "note": f"withheld: {val:.2f} outside plausible range "
                            f"[{lo},{hi}] (possible source unit mismatch)"}
    return {"value": round(val, 2), "included": included, "skipped": skipped, "note": note}


def aggregate_sector(uni: SectorUniverse, companies: list[Company],
                     attempted: int, as_of: str) -> dict:
    """Build one sector's snapshot from its constituent Company records."""
    # A company "counts" toward the sector if it yielded any usable field.
    usable = [c for c in companies
              if any(getattr(c, f) is not None for f in
                     ("net_income", "equity", "total_assets", "market_cap"))]
    dropped = [c for c in companies if c not in usable]

    metrics: dict[str, dict] = {}
    # P/E excludes loss-makers from BOTH sums: net_income is the denominator, so
    # den_positive drops any constituent with net_income <= 0 from both sides.
    metrics["pe"] = _metric(usable, lambda c: c.market_cap, lambda c: c.net_income,
                            den_positive=True, bound_key="pe",
                            den_skip="loss-making (excluded from P/E)")
    metrics["pb"] = _metric(usable, lambda c: c.market_cap, lambda c: c.equity,
                            den_positive=True, bound_key="pb")
    metrics["roe"] = _metric(usable, lambda c: c.net_income, lambda c: c.equity,
                             den_positive=True, as_percent=True, bound_key="roe")
    metrics["roa"] = _metric(usable, lambda c: c.net_income, lambda c: c.total_assets,
                             den_positive=True, as_percent=True, bound_key="roa")

    applicable, reason = metric_applicable(uni.key, "roce")
    if not applicable:
        metrics["roce"] = {"value": None, "applicable": False, "reason": reason,
                           "included": [], "skipped": []}
    else:
        metrics["roce"] = _metric(usable, lambda c: c.ebit(), lambda c: c.capital_employed(),
                                  den_positive=True, as_percent=True, bound_key="roce")
        metrics["roce"]["applicable"] = True

    # Pooled trailing earnings growth (input to PEG): companies that report both
    # the current and a positive prior-year net income.
    g_pairs = [c for c in usable if c.net_income is not None
               and c.net_income_prior is not None and c.net_income_prior > 0]
    growth = None
    if g_pairs:
        cur = sum(c.net_income for c in g_pairs)
        prior = sum(c.net_income_prior for c in g_pairs)
        if prior > 0:
            growth = round((cur - prior) / prior * 100.0, 2)
    metrics["earnings_growth"] = {
        "value": growth,
        "included": [c.symbol for c in g_pairs],
        "note": "" if growth is not None else "insufficient prior-year data",
    }

    # Trailing PEG = Sector P/E ÷ pooled earnings growth %. Withheld when growth
    # is not positive (PEG is meaningless then) or the result is implausible.
    pe_val = metrics["pe"]["value"]
    peg_val, peg_note = None, ""
    if pe_val is None:
        peg_note = "no sector P/E"
    elif growth is None:
        peg_note = "no earnings-growth figure"
    elif growth <= 0:
        peg_note = f"earnings growth not positive ({growth}%); PEG not meaningful"
    else:
        cand = pe_val / growth
        if 0 < cand <= 20:
            peg_val = round(cand, 2)
        else:
            peg_note = f"withheld: PEG {cand:.2f} outside plausible range (0,20]"
    metrics["peg"] = {"value": peg_val, "growth_pct": growth, "note": peg_note,
                      "basis": "trailing (P/E ÷ latest annual earnings growth)"}

    mcaps = [c.market_cap for c in usable if c.market_cap is not None]
    total_mcap = round(sum(mcaps), 2) if mcaps else None
    avg_mcap = round(sum(mcaps) / len(mcaps), 2) if mcaps else None

    # Data period = the most common annual period-end across the constituents,
    # so the UI can state which fiscal year the sector figures come from.
    periods = [c.period_label for c in usable if getattr(c, "period_label", None)]
    data_period = max(set(periods), key=periods.count) if periods else None

    skip_detail = [{"symbol": c.symbol, "reason": "; ".join(c.missing) or "no usable data"}
                   for c in dropped]

    return {
        "sector": uni.name,
        "sector_key": uni.key,
        "reference_index": uni.index_label,
        "source": SOURCE_LABEL,
        "as_of": as_of,
        "data_period": data_period,
        "methodology": METHODOLOGY,
        "constituents_attempted": attempted,
        "constituents_included": len(usable),
        "constituents_skipped": attempted - len(usable),
        "skipped_detail": skip_detail,
        "pulse": {
            "no_companies": len(usable),
            "total_market_cap": total_mcap,
            "avg_market_cap": avg_mcap,
        },
        "metrics": {**{m: metrics[m] for m in METRICS},
                    "peg": metrics["peg"],
                    "earnings_growth": metrics["earnings_growth"]},
    }


def new_snapshot() -> dict:
    """An empty top-level snapshot container."""
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": SOURCE_LABEL,
        "methodology": METHODOLOGY,
        "sectors": {},
    }
