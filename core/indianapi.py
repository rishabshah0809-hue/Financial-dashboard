"""
indianapi.py
------------
Pure, offline extraction and metric engine for the Sector Lens.

This module never makes a network call and never invents a number. It takes a
*raw* IndianAPI ``/stock`` JSON response (exactly the shape saved by the
inspector as ``api/indianapi_*_raw.json``) and pulls out only the fields that
were verified against the real HDFC Bank and TCS responses. Anything that is not
present in the source is returned as ``None`` and rendered as an em dash ("—")
upstream — it is never estimated.

Field mapping (locked against real responses)
=============================================
All absolute money values below come from the **financial statement map**
(``financials[].stockFinancialMap`` INC/BAL/CAS) and ``marketCap`` — these are
in ₹ crore and mutually self-consistent. The ``keyMetrics.*`` block is used only
for **ratios** (P/E, P/B, ROE, ROA, margins, growth, turnover); its absolute
money figures are on a different (×10) scale and are deliberately not summed.

  Company name        companyName                                    DIRECT
  NSE symbol          companyProfile.exchangeCodeNse                 DIRECT
  Market cap (₹cr)    stockDetailsReusableData.marketCap             DIRECT
  Net income          INC[NetIncome]                                 DIRECT
  Revenue             INC[Revenue] (non-fin) / interest+non-int      DIRECT / n.a.
  Operating profit    INC[OperatingIncome]  (== EBIT)                DIRECT (non-fin)
  D & A               INC[Depreciation/Amortization]                 DIRECT (non-fin)
  Total equity        BAL[TotalEquity]                               DIRECT
  Total assets        BAL[TotalAssets]                               DIRECT
  Total debt          BAL[TotalDebt]                                 DIRECT
  EPS (FY)            INC[DilutedEPSExcludingExtraOrdItems]          DIRECT
  Prev-year revenue   prior Annual INC[Revenue]                      DERIVED
  Prev-year EPS       prior Annual INC[DilutedEPSExcludingExtraOrd]  DERIVED

Per-company display ratios (Top 10), read from keyMetrics:
  OPM                 margins[operatingMarginTrailing12Month]        DIRECT
  NPM                 margins[netProfitMarginPercentTrailing12Month] DIRECT
  EPS TTM growth      growth[ePSChangePercentTTMOverTTM]             DIRECT
  Asset turnover      mgmtEffectiveness[assetTurnoverTrailing12Month]DIRECT / None
  Interest coverage   financialstrength[netInterestCoverage…]        DIRECT / None
  Debt / equity       financialstrength[totalDebtPerTotalEquity…FY]  DIRECT
  Op. rev growth TTM  growth[revenueChangePercentTTMPOverTTM]        DIRECT
  P/E TTM             valuation[pPerEBasicExcludingExtraordinary…TTM]DIRECT
  P/B                 valuation[priceToBookMostRecentFiscalYear]     DIRECT
  ROE (display)       mgmtEffectiveness[returnOnAverageEquity…TTM]   DIRECT

Sector aggregates use the pooled, year-end methodology (documented in
``METHODOLOGY``): sums across constituents of the *latest common annual period*,
never an average of per-company ratios.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Documented methodology — copied verbatim into the snapshot metadata so the
# numbers are always reproducible and the pooled-vs-average difference is stated.
# ---------------------------------------------------------------------------
METHODOLOGY: dict[str, str] = {
    "pe": "Sector P/E = Σ MarketCap / Σ NetIncome; loss-making companies "
          "(NetIncome ≤ 0) excluded from both numerator and denominator.",
    "pb": "Sector P/B = Σ MarketCap / Σ TotalEquity (year-end equity).",
    "roe": "Sector ROE = Σ NetIncome / Σ TotalEquity, year-end balances. "
           "Pooled — may differ from IndianAPI per-company ROE, which uses "
           "average balances. This divergence is intentional.",
    "roa": "Sector ROA = Σ NetIncome / Σ TotalAssets, year-end balances. "
           "Pooled — may differ from IndianAPI per-company ROA (average "
           "balances). Intentional.",
    "roce": "Sector ROCE = Σ OperatingIncome / Σ (TotalEquity + TotalDebt). "
            "OperatingIncome (== EBIT) is taken directly from IndianAPI; EBIT "
            "is NOT reconstructed from EBITDA − D&A when OperatingIncome exists. "
            "Not meaningful for lenders — Banking & Finance ROCE is '—'.",
    "period": "All aggregates use the latest annual period common to the "
              "constituents. Values are ₹ crore from the statement map.",
    "units": "Absolute values are ₹ crore from financials[].stockFinancialMap "
             "and marketCap. keyMetrics is used only for ratios.",
}

DASH = "—"  # what a genuinely unavailable value renders as
LENDER_ROCE_REASON = "ROCE is not a meaningful metric for lenders."


# ---------------------------------------------------------------------------
# small parsing helpers — a value is either a clean float or None, never a guess
# ---------------------------------------------------------------------------
def _f(value) -> float | None:
    """A finite float, or None. IndianAPI encodes numbers as strings and uses
    the literal 'None' / null / '' for missing — all of which become None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if f == f and f not in (float("inf"), float("-inf")) else None
    s = str(value).strip().replace(",", "")
    if s == "" or s.lower() in ("none", "null", "nan", "-"):
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _map(items: list) -> dict[str, str]:
    """A statement section (list of {key,value}) → {key: value}."""
    out: dict[str, str] = {}
    for it in items or []:
        if isinstance(it, dict) and "key" in it:
            out[it["key"]] = it.get("value")
    return out


def _km(raw: dict, category: str) -> dict[str, str]:
    """A keyMetrics category (list of {key,value}) → {key: value}."""
    cat = (raw.get("keyMetrics") or {}).get(category)
    return _map(cat) if isinstance(cat, list) else {}


# ---------------------------------------------------------------------------
# one company, distilled to exactly the fields the Lens needs
# ---------------------------------------------------------------------------
@dataclass
class Company:
    name: str
    nse_symbol: str
    market_cap: float | None            # ₹ crore
    fiscal_year: str | None
    period_end: str | None
    # pooled-aggregate inputs (₹ crore, year-end / annual)
    net_income: float | None
    total_equity: float | None
    total_assets: float | None
    total_debt: float | None
    operating_income: float | None      # EBIT, non-financial only
    depreciation: float | None
    revenue: float | None
    prev_revenue: float | None
    eps: float | None
    prev_eps: float | None
    # per-company display ratios (Top 10), each already a clean float or None
    ratios: dict[str, float | None] = field(default_factory=dict)
    raw_industry: str | None = None

    @property
    def profitable(self) -> bool:
        return self.net_income is not None and self.net_income > 0

    def nse_url(self) -> str:
        """Official NSE quote page for this symbol (never a third-party site)."""
        return f"https://www.nseindia.com/get-quotes/equity?symbol={self.nse_symbol}"


def _annual_periods(raw: dict) -> list[dict]:
    """Annual statement blocks, newest fiscal year first."""
    fins = raw.get("financials")
    if not isinstance(fins, list):
        return []
    annual = [e for e in fins if isinstance(e, dict) and e.get("Type") == "Annual"]
    annual.sort(key=lambda e: str(e.get("FiscalYear") or ""), reverse=True)
    return annual


def parse_company(raw: dict) -> Company | None:
    """Raw IndianAPI /stock JSON → Company. Returns None if the response is
    missing the identity fields (name / NSE symbol) — such a response is
    unusable and is skipped by the pipeline, never patched with placeholders."""
    if not isinstance(raw, dict):
        return None
    name = (raw.get("companyName") or "").strip()
    profile = raw.get("companyProfile") or {}
    nse = (profile.get("exchangeCodeNse") or "").strip()
    if not name or not nse:
        return None

    reuse = raw.get("stockDetailsReusableData") or {}
    market_cap = _f(reuse.get("marketCap"))

    periods = _annual_periods(raw)
    latest = periods[0] if periods else {}
    prior = periods[1] if len(periods) > 1 else {}
    smap = latest.get("stockFinancialMap") or {}
    inc, bal = _map(smap.get("INC")), _map(smap.get("BAL"))
    prior_inc = _map((prior.get("stockFinancialMap") or {}).get("INC"))

    val = _km(raw, "valuation")
    margins = _km(raw, "margins")
    mgmt = _km(raw, "mgmtEffectiveness")
    strength = _km(raw, "financialstrength")
    growth = _km(raw, "growth")

    ratios = {
        "opm": _f(margins.get("operatingMarginTrailing12Month")),
        "npm": _f(margins.get("netProfitMarginPercentTrailing12Month")),
        "eps_ttm_growth": _f(growth.get("ePSChangePercentTTMOverTTM")),
        "asset_turnover": _f(mgmt.get("assetTurnoverTrailing12Month")),
        "interest_coverage": _f(strength.get("netInterestCoverageTrailing12Month")),
        "debt_to_equity": _f(strength.get("totalDebtPerTotalEquityMostRecentFiscalYear")),
        "op_rev_growth_ttm": _f(growth.get("revenueChangePercentTTMPOverTTM")),
        "pe_ttm": _f(val.get("pPerEBasicExcludingExtraordinaryItemsTTM")),
        "pb": _f(val.get("priceToBookMostRecentFiscalYear")),
        "roe": _f(mgmt.get("returnOnAverageEquityTrailing12Month")),
    }

    return Company(
        name=name,
        nse_symbol=nse,
        market_cap=market_cap,
        fiscal_year=str(latest.get("FiscalYear")) if latest.get("FiscalYear") else None,
        period_end=latest.get("EndDate"),
        net_income=_f(inc.get("NetIncome")),
        total_equity=_f(bal.get("TotalEquity")),
        total_assets=_f(bal.get("TotalAssets")),
        total_debt=_f(bal.get("TotalDebt")),
        operating_income=_f(inc.get("OperatingIncome")),
        depreciation=_f(inc.get("Depreciation/Amortization")),
        revenue=_f(inc.get("Revenue")),
        prev_revenue=_f(prior_inc.get("Revenue")),
        eps=_f(inc.get("DilutedEPSExcludingExtraOrdItems")),
        prev_eps=_f(prior_inc.get("DilutedEPSExcludingExtraOrdItems")),
        ratios=ratios,
        raw_industry=raw.get("industry"),
    )


# ---------------------------------------------------------------------------
# pooled sector aggregates — sums only, never an average of ratios
# ---------------------------------------------------------------------------
def _round(x: float | None, nd: int) -> float | None:
    return None if x is None else round(x, nd)


def pooled_metrics(companies: list[Company], *, is_financial: bool) -> dict:
    """Compute the pooled sector aggregates. Every result is either a real
    number derived from real inputs, or None (→ '—'). Loss-makers are excluded
    from P/E; ROCE is None for financial sectors by definition."""
    def total(attr, predicate=lambda c: True) -> tuple[float, int]:
        s, n = 0.0, 0
        for c in companies:
            v = getattr(c, attr)
            if v is not None and predicate(c):
                s += v
                n += 1
        return s, n

    # P/E — exclude loss-makers from BOTH sides
    mc_prof, n_pe = 0.0, 0
    ni_prof = 0.0
    for c in companies:
        if c.profitable and c.market_cap is not None:
            mc_prof += c.market_cap
            ni_prof += c.net_income
            n_pe += 1
    pe = (mc_prof / ni_prof) if n_pe and ni_prof > 0 else None

    sum_mc_eq, _ = total("market_cap", lambda c: c.total_equity is not None)
    sum_eq_for_mc = sum(c.total_equity for c in companies
                        if c.total_equity is not None and c.market_cap is not None)
    sum_mc_all = sum(c.market_cap for c in companies
                     if c.market_cap is not None and c.total_equity is not None)
    pb = (sum_mc_all / sum_eq_for_mc) if sum_eq_for_mc > 0 else None

    # ROE / ROA — pair NI with the matching balance so sums stay consistent
    ni_e = sum(c.net_income for c in companies
               if c.net_income is not None and c.total_equity is not None)
    eq = sum(c.total_equity for c in companies
             if c.net_income is not None and c.total_equity is not None)
    roe = (ni_e / eq * 100) if eq > 0 else None

    ni_a = sum(c.net_income for c in companies
               if c.net_income is not None and c.total_assets is not None)
    assets = sum(c.total_assets for c in companies
                 if c.net_income is not None and c.total_assets is not None)
    roa = (ni_a / assets * 100) if assets > 0 else None

    # ROCE — non-financial only, EBIT from OperatingIncome directly
    roce = None
    roce_reason = None
    if is_financial:
        roce_reason = LENDER_ROCE_REASON
    else:
        ebit = sum(c.operating_income for c in companies
                   if c.operating_income is not None
                   and c.total_equity is not None and c.total_debt is not None)
        cap = sum((c.total_equity + c.total_debt) for c in companies
                  if c.operating_income is not None
                  and c.total_equity is not None and c.total_debt is not None)
        roce = (ebit / cap * 100) if cap > 0 else None

    return {
        "pe": _round(pe, 2),
        "pb": _round(pb, 2),
        "roe": _round(roe, 2),
        "roa": _round(roa, 2),
        "roce": _round(roce, 2),
        "roce_note": roce_reason,
        "companies_in_pe": n_pe,
    }


def top_metrics(c: Company, *, is_financial: bool) -> dict:
    """The 14 Top-10 fields for one company. ROCE per company is '—' for
    financial sectors; otherwise it is OperatingIncome / (Equity + Debt)."""
    if is_financial:
        roce = None
    elif (c.operating_income is not None and c.total_equity is not None
          and c.total_debt is not None and (c.total_equity + c.total_debt) > 0):
        roce = round(c.operating_income / (c.total_equity + c.total_debt) * 100, 2)
    else:
        roce = None

    # Revenue growth YoY — derived from the two most recent annual revenues
    if (c.revenue is not None and c.prev_revenue not in (None, 0)
            and c.prev_revenue > 0):
        rev_growth = round((c.revenue / c.prev_revenue - 1) * 100, 2)
    else:
        rev_growth = None

    return {
        "name": c.name,
        "nse_symbol": c.nse_symbol,
        "nse_url": c.nse_url(),
        "market_cap": _round(c.market_cap, 2),
        "opm": c.ratios.get("opm"),
        "eps_ttm_growth": c.ratios.get("eps_ttm_growth"),
        "asset_turnover": c.ratios.get("asset_turnover"),
        "interest_coverage": c.ratios.get("interest_coverage"),
        "debt_to_equity": c.ratios.get("debt_to_equity"),
        "roe": c.ratios.get("roe"),
        "roce": roce,
        "npm": c.ratios.get("npm"),
        "revenue_growth_yoy": rev_growth,
        "op_rev_growth_ttm": c.ratios.get("op_rev_growth_ttm"),
        "pb": c.ratios.get("pb"),
        "pe": c.ratios.get("pe_ttm"),
    }


def rank_top(companies: list[Company], n: int, *, is_financial: bool) -> list[dict]:
    """Top-N by real market cap, reusing the already-fetched responses (no extra
    API calls). Companies without a market cap cannot be ranked and are dropped
    from the ranking (they are still counted as skipped upstream)."""
    ranked = sorted(
        (c for c in companies if c.market_cap is not None),
        key=lambda c: c.market_cap, reverse=True)
    return [top_metrics(c, is_financial=is_financial) for c in ranked[:n]]
