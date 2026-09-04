"""
tilt.py
-------
Per-sector structural cycle & seasonality profiles + data-grounded monthly tilt.

A. Structural seasonality: a stable, sector-specific description of how each
   niche NSE industry typically behaves across the cycle and through the year.
B. Current monthly tilt: recomputed each snapshot from real month-over-month
   deltas (earnings growth, revenue growth, ROE, ROA, valuation, constituents).

Current tilt is strictly one of: Expansion, Recovery, Stable, Mid-cycle,
Slowdown, Mixed.
"""

from __future__ import annotations

TILTS = ("Expansion", "Recovery", "Stable", "Mid-cycle", "Slowdown", "Mixed")

# Per niche-sector structural cycle & seasonality. One entry per sector key in
# core.sector_universe. No generic catch-all: every sector has its own read.
SECTOR_CYCLE: dict[str, dict] = {
    "bank": {"nature": "Cyclical", "seasonal": True, "text": (
        "Banks are geared to the credit and interest-rate cycle. Loan growth, net "
        "interest margins (NIM) and credit costs move with GDP and RBI policy; "
        "asset-quality stress and provisioning surface late-cycle after tightening. "
        "Disbursements build through H2 (Oct–Mar) around the festive season and the "
        "fiscal year-end, and Q4 (Jan–Mar) is usually the strongest quarter.")},
    "nbfc": {"nature": "Cyclical", "seasonal": True, "text": (
        "NBFCs live off loan growth, funding costs, spreads and credit quality. They "
        "lead banks in a recovery (nimbler underwriting) but are more exposed to "
        "funding shocks and liquidity tightening. Watch borrowing cost vs lending "
        "yield (spread) and asset-quality in unsecured/vehicle books; festive H2 lifts "
        "consumer and vehicle lending.")},
    "housing_finance": {"nature": "Rate-sensitive cyclical", "seasonal": True, "text": (
        "Housing finance tracks home-loan demand, interest rates and property "
        "affordability. Growth accelerates when rates ease and real-estate demand "
        "firms; spreads and asset quality drive earnings. Bookings and disbursements "
        "cluster around the festive season and the fiscal year-end.")},
    "insurance": {"nature": "Structural-growth / rate-sensitive", "seasonal": True, "text": (
        "Insurers are driven by premium growth (APE/VNB for life; loss and combined "
        "ratios for general), persistency and investment income, which is sensitive to "
        "interest rates and equity markets. March (Q4) sees a strong tax-driven push "
        "in life premiums.")},
    "financial_services": {"nature": "Cyclical (broad)", "seasonal": True, "text": (
        "A broad financials basket spanning banks, NBFCs, insurers and capital-market "
        "names — its cyclicality blends the credit cycle, rate cycle and market cycle. "
        "Treat it as a sector-of-sectors: read the underlying banking, NBFC and "
        "insurance dynamics rather than a single driver.")},
    "it": {"nature": "Cyclical (global)", "seasonal": True, "text": (
        "IT services is a structural-growth sector but cyclical to global (US/Europe) "
        "technology budgets and the USD/INR rate. Deal signings, discretionary spend "
        "and utilisation soften in global slowdowns and re-accelerate in recoveries. "
        "H1 (Apr–Sep) is seasonally strong on budget flush; Q3 (Oct–Dec) is softer on "
        "furloughs. A weaker rupee is a margin tailwind.")},
    "telecom": {"nature": "Structural-growth utility-like", "seasonal": True, "text": (
        "Telecom is capital-intensive and consolidated; earnings turn on ARPU, "
        "subscriber additions, data usage and tariff moves, set against heavy spectrum "
        "and network capex and leverage. Tariff hikes and 5G monetisation are the key "
        "swing factors; seasonality is mild.")},
    "media": {"nature": "Cyclical (discretionary)", "seasonal": True, "text": (
        "Media & entertainment is discretionary and ad-spend led, so it tracks the "
        "consumption cycle and corporate ad budgets. Subscription, box-office and "
        "digital-streaming trends drive the mix. Festive Q3 and election/sporting years "
        "lift advertising; weak-demand years compress it first.")},
    "pharma": {"nature": "Defensive", "seasonal": True, "text": (
        "Pharma is largely defensive — domestic drug demand is inelastic to the cycle. "
        "The US-generics business carries its own pricing cycle (price erosion vs new "
        "launches) and USFDA regulatory events drive company-specific swings. Acute "
        "therapies (anti-infectives, respiratory) peak in the monsoon and winter "
        "(Q2–Q3).")},
    "hospitals": {"nature": "Defensive structural-growth", "seasonal": True, "text": (
        "Hospitals are defensive with structural growth from rising healthcare demand "
        "and insurance penetration. Earnings turn on occupancy (ARPOB), case mix, bed "
        "additions and new-hospital ramp-up. Elective procedures dip around festivals "
        "and peak in cooler months; largely non-cyclical.")},
    "healthcare": {"nature": "Defensive (broad)", "seasonal": True, "text": (
        "A broad healthcare basket blending pharma, hospitals, diagnostics and medical "
        "devices — defensive overall, with the pharma pricing cycle and hospital "
        "occupancy as the main sub-drivers. Read the underlying pharma vs hospital mix.")},
    "auto": {"nature": "Cyclical", "seasonal": True, "text": (
        "Autos run on the volume cycle, commodity (steel/aluminium) costs, financing "
        "conditions and new-model launches. Volumes rise with the economy and compress "
        "in slowdowns; margins swing with input prices. Festive Q3 (Oct–Dec) is the "
        "strongest quarter; monsoon months are softer.")},
    "fmcg": {"nature": "Defensive", "seasonal": True, "text": (
        "FMCG & staples is defensive — everyday-essentials demand holds across the "
        "cycle. What moves it is seasonal and input-cost driven: rural demand tracks "
        "the monsoon and harvest, the festive H2 lifts volumes, summer supports "
        "beverages, and margins swing with commodity cycles (palm oil, crude, grains).")},
    "consumer_durables": {"nature": "Cyclical (discretionary)", "seasonal": True, "text": (
        "Consumer durables are discretionary and tied to income, financing and "
        "housing. Summer (Q1) drives cooling products, and the festive Q3 drives "
        "big-ticket buying; commodity and currency moves swing margins. Demand softens "
        "first in slowdowns.")},
    "consumer_services": {"nature": "Cyclical (discretionary)", "seasonal": True, "text": (
        "A broad consumer-services basket (hospitality, travel, QSR, education, "
        "platforms) tied to discretionary spend and confidence. Travel and hospitality "
        "peak in holiday and wedding seasons; the model runs on footfalls and "
        "same-store growth, which lead the cycle.")},
    "retail": {"nature": "Cyclical (discretionary)", "seasonal": True, "text": (
        "Retail & consumption is cyclical, tied to discretionary spend, inflation and "
        "confidence. Seasonality is pronounced: the festive Q3 (Oct–Dec, Diwali) is the "
        "peak quarter, weddings support Q4, and monsoon months are soft. Margins are "
        "thin and sensitive to discounting in weak demand.")},
    "realty": {"nature": "Highly cyclical", "seasonal": True, "text": (
        "Real estate is highly cyclical and rate-sensitive, moving in multi-year "
        "up/down cycles driven by affordability, interest rates and inventory. Launches "
        "and bookings cluster around the festive season (Q3) and registrations rise "
        "near the fiscal year-end. Falling rates and low unsold inventory mark the "
        "up-phase.")},
    "metal": {"nature": "Highly cyclical (global)", "seasonal": True, "text": (
        "Metals & mining is highly cyclical and geared to global commodity prices, "
        "Chinese demand and the USD. Earnings swing with LME/steel prices and spreads "
        "over input costs; heavy fixed costs amplify the cycle. Little intra-year "
        "seasonality — the commodity price cycle dominates.")},
    "cement": {"nature": "Cyclical", "seasonal": True, "text": (
        "Cement tracks construction and infrastructure demand, realisations, fuel "
        "(petcoke/coal) costs and capacity utilisation. It is a regional, freight- and "
        "energy-intensive business. Demand and dispatches slow through the monsoon (Q2) "
        "and accelerate into the Jan–Mar construction push.")},
    "chemicals": {"nature": "Cyclical", "seasonal": True, "text": (
        "Chemicals is cyclical and geared to global demand, feedstock (crude "
        "derivatives) costs and China supply. Specialty names are steadier than "
        "commodity chemicals; realisations, spreads and inventory de-stocking drive the "
        "cycle. Export demand and currency matter more than intra-year seasonality.")},
    "oil_gas": {"nature": "Cyclical (commodity)", "seasonal": True, "text": (
        "Oil & gas turns on crude prices, refining (GRM) and marketing margins, gas "
        "prices and regulated/administered pricing. Upstream gains from high crude; "
        "OMCs benefit from strong marketing margins and are squeezed when prices spike "
        "under price controls. The global energy cycle dominates.")},
    "power": {"nature": "Cyclical / regulated", "seasonal": True, "text": (
        "Power spans regulated utilities (stable, return-on-equity models) and merchant "
        "/ renewable generation and financing. Demand tracks industrial activity and "
        "peaks in summer; the sector is capex- and rate-sensitive, and renewables add a "
        "structural-growth tilt on top of the utility base.")},
    "capital_goods": {"nature": "Highly cyclical (capex)", "seasonal": True, "text": (
        "Capital goods is highly cyclical and capex-led, geared to the government and "
        "private investment cycle. Order inflows, order-book execution and margins are "
        "the key reads; long-gestation, debt-funded projects make it rate-sensitive. "
        "Execution accelerates into the Jan–Mar fiscal year-end.")},
    "construction": {"nature": "Highly cyclical (capex)", "seasonal": True, "text": (
        "Construction & EPC is highly cyclical and order-book driven, tied to the "
        "infrastructure and capex cycle and to government spending. Working capital and "
        "leverage matter; execution slows through the monsoon (Q2) and peaks in the "
        "Jan–Mar year-end push.")},
    "commercial_transport": {"nature": "Cyclical (trade-linked)", "seasonal": True, "text": (
        "Commercial & transport services (ports, logistics, aviation, shipping) is "
        "geared to trade volumes, GDP and fuel costs. Volumes and freight/passenger "
        "yields lead the cycle; fuel is the main margin swing. Travel and freight peak "
        "in the festive and year-end months.")},
    "commodities": {"nature": "Highly cyclical (broad)", "seasonal": True, "text": (
        "A broad commodities basket (metals, oil & gas, cement, chemicals) — highly "
        "cyclical and geared to global prices, the USD and demand. Used here as a "
        "fallback benchmark; read the specific underlying commodity's price cycle.")},
    "infrastructure": {"nature": "Highly cyclical (broad)", "seasonal": True, "text": (
        "A broad infrastructure basket spanning power, telecom, construction and "
        "capital goods — highly cyclical and capex-led, sensitive to interest rates and "
        "the government investment cycle. Used here as a fallback benchmark for "
        "diversified names.")},
}

_DEFAULT_CYCLE = {"nature": "Mixed", "seasonal": False, "text": (
    "No sector-specific structural profile is available for this universe.")}

STRUCTURAL = {k: v["text"] for k, v in SECTOR_CYCLE.items()}


def profile(sector_key: str | None) -> dict:
    """Return the structural cycle profile for a sector (safe default if unknown)."""
    return SECTOR_CYCLE.get(sector_key or "", _DEFAULT_CYCLE)


def _delta(new, old) -> float | None:
    if isinstance(new, (int, float)) and isinstance(old, (int, float)):
        return round(new - old, 2)
    return None


def current_tilt(sector_key: str, current: dict, previous: dict | None) -> dict:
    """Classify the current monthly tilt from real month-over-month data."""
    prof = profile(sector_key)
    structural = prof["text"]

    if not previous:
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
            "current_tilt": tilt, "tilt_reason": reason,
            "structural_seasonality": structural, "nature": prof["nature"],
            "seasonal": prof["seasonal"], "signals": {},
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
    cur_top = {r.get("nse_symbol") for r in current.get("constituents", []) if r.get("nse_symbol")}
    prev_top = {r.get("nse_symbol") for r in previous.get("constituents", []) if r.get("nse_symbol")}
    signals["constituents_changed"] = len(cur_top - prev_top) if cur_top and prev_top else 0

    usable = {k: v for k, v in signals.items() if v is not None and k != "constituents_changed"}
    if not usable:
        return {
            "current_tilt": "Stable",
            "tilt_reason": "No metric deltas available between snapshots; fundamentals remain stable.",
            "structural_seasonality": structural, "nature": prof["nature"],
            "seasonal": prof["seasonal"], "signals": signals,
        }

    up_count = sum(1 for k in ("roe", "roa", "earnings_growth", "revenue_growth")
                   if signals.get(k) is not None and signals[k] > 0.10)
    down_count = sum(1 for k in ("roe", "roa", "earnings_growth", "revenue_growth")
                     if signals.get(k) is not None and signals[k] < -0.10)

    if up_count >= 2 and down_count == 0:
        tilt, reason = "Expansion", _format_reason("expanded", signals)
    elif up_count > 0 and down_count == 0:
        tilt, reason = "Recovery", _format_reason("recovering", signals)
    elif down_count >= 2 and up_count == 0:
        tilt, reason = "Slowdown", _format_reason("softening", signals)
    elif up_count > 0 and down_count > 0:
        tilt, reason = "Mixed", "Mixed signals: some sector metrics improved while others moderated versus the previous month."
    else:
        cur_eg = current.get("earnings_growth")
        if isinstance(cur_eg, (int, float)) and cur_eg > 0:
            tilt = "Mid-cycle"
            reason = f"Metrics are steady with positive earnings growth ({cur_eg:+.1f}%), consistent with mid-cycle dynamics."
        else:
            tilt = "Stable"
            reason = "Sector fundamentals are broadly unchanged versus the previous month."

    return {
        "current_tilt": tilt, "tilt_reason": reason,
        "structural_seasonality": structural, "nature": prof["nature"],
        "seasonal": prof["seasonal"], "signals": signals,
    }


def _format_reason(verb: str, signals: dict) -> str:
    parts = []
    eg, rg, roe = signals.get("earnings_growth"), signals.get("revenue_growth"), signals.get("roe")
    if eg is not None and abs(eg) > 0.05:
        parts.append(f"earnings growth delta {eg:+.1f} pts")
    if rg is not None and abs(rg) > 0.05:
        parts.append(f"revenue growth delta {rg:+.1f} pts")
    if roe is not None and abs(roe) > 0.05:
        parts.append(f"sector ROE {roe:+.1f} pts")
    if parts:
        return f"Current tilt ({verb}) driven by: " + ", ".join(parts) + "."
    return f"Sector fundamentals {verb} compared to the previous snapshot."
