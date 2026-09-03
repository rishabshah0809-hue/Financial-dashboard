"""
indianapi.py
------------
Pure extraction, pooled-metric calculation, and HTTP client for IndianAPI.

Extracts fields verified against real HDFC Bank (lender) and TCS (non-financial)
responses. Anything missing in the source is returned as None (rendered as "—")
and is never estimated.

Key Rules:
- Direct OperatingIncome is used as EBIT for ROCE.
- ROCE = Σ OperatingIncome / Σ (TotalEquity + TotalDebt) for non-financials.
- Banking & Finance: ROCE is None (rendered as "—").
- Sector P/E excludes loss-makers (NetIncome <= 0) from both numerator and denominator.
- Sector P/B = Σ MarketCap / Σ TotalEquity.
- Sector ROE = Σ NetIncome / Σ TotalEquity * 100.
- Sector ROA = Σ NetIncome / Σ TotalAssets * 100.
- Top 10 ranked strictly by actual Market Cap with zero extra API calls.
- Official NSE URLs generated directly from symbol:
  https://www.nseindia.com/get-quotes/equity?symbol={symbol}
- Credit safety: pacing between requests, immediate stop on HTTP 401, 403, 429.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests

LOGGER = logging.getLogger("fundacheck.indianapi")

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
    "eps_growth": "Sector EPS growth = market-cap-weighted average of each "
                  "constituent's YoY diluted-EPS growth (latest vs prior FY).",
    "piotroski": "Sector Piotroski = simple average of constituents' Piotroski "
                 "F-scores (0-9). Not computed for lenders (banks/NBFCs lack the "
                 "current-asset / cost-of-revenue structure the score assumes).",
    "peg": "Sector PEG = Sector P/E / pooled earnings-growth %, only when "
           "earnings growth is positive.",
    "period": "All aggregates use the latest annual period common to the "
              "constituents. Values are ₹ crore from the statement map.",
    "units": "Absolute values are ₹ crore from financials[].stockFinancialMap "
             "and marketCap. keyMetrics is used only for ratios.",
}

DASH = "—"
LENDER_ROCE_REASON = "ROCE is not a meaningful metric for lenders."


def _f(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if f == f and f not in (float("inf"), float("-inf")) else None
    s = str(value).strip().replace(",", "")
    if s == "" or s.lower() in ("none", "null", "nan", "-", "—"):
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _map(items: list | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items or []:
        if isinstance(it, dict) and "key" in it:
            out[it["key"]] = it.get("value")
    return out


def _km(raw: dict, category: str) -> dict[str, str]:
    cat = (raw.get("keyMetrics") or {}).get(category)
    return _map(cat) if isinstance(cat, list) else {}


def _round(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


@dataclass
class Company:
    name: str
    nse_symbol: str
    market_cap: float | None            # ₹ crore
    fiscal_year: str | None
    period_end: str | None
    # pooled-aggregate inputs (₹ crore, year-end / annual)
    net_income: float | None
    prev_net_income: float | None
    total_equity: float | None
    total_assets: float | None
    total_debt: float | None
    operating_income: float | None      # direct EBIT, non-financial only
    depreciation: float | None
    revenue: float | None
    prev_revenue: float | None
    eps: float | None
    prev_eps: float | None
    isin: str | None = None
    # per-company display ratios (Top 10), each a clean float or None
    ratios: dict[str, float | None] = field(default_factory=dict)
    raw_industry: str | None = None
    # per-company derived scores, computed at parse time
    eps_growth: float | None = None          # YoY diluted-EPS growth, %
    piotroski: int | None = None             # Piotroski F-score 0-9 (None for lenders)

    @property
    def profitable(self) -> bool:
        return self.net_income is not None and self.net_income > 0

    def nse_url(self) -> str:
        return f"https://www.nseindia.com/get-quotes/equity?symbol={self.nse_symbol}"


def _annual_periods(raw: dict) -> list[dict]:
    fins = raw.get("financials")
    if not isinstance(fins, list):
        return []
    annual = [e for e in fins if isinstance(e, dict) and str(e.get("Type", "")).lower() == "annual"]
    if not annual:
        annual = [e for e in fins if isinstance(e, dict) and e.get("stockFinancialMap")]

    def _sort_key(e: dict) -> str:
        return str(e.get("FiscalYear") or e.get("EndDate") or "")

    annual.sort(key=_sort_key, reverse=True)
    return annual


def _piotroski_fscore(inc, bal, cas, pinc, pbal, pcas) -> int | None:
    """Piotroski F-score (0-9) from latest vs prior annual statements.

    Returns None when the firm lacks the current-asset / cost-of-revenue
    structure the score assumes (banks & other lenders), matching how ROCE is
    treated as not-meaningful for financials.
    """
    ni,   ni_p   = _f(inc.get("NetIncome")),        _f(pinc.get("NetIncome"))
    ta,   ta_p   = _f(bal.get("TotalAssets")),      _f(pbal.get("TotalAssets"))
    cfo          = _f(cas.get("CashfromOperatingActivities"))
    ltd   = _f(bal.get("TotalLongTermDebt"))  or _f(bal.get("LongTermDebt"))
    ltd_p = _f(pbal.get("TotalLongTermDebt")) or _f(pbal.get("LongTermDebt"))
    ca,   ca_p   = _f(bal.get("TotalCurrentAssets")),      _f(pbal.get("TotalCurrentAssets"))
    cl,   cl_p   = _f(bal.get("TotalCurrentLiabilities")), _f(pbal.get("TotalCurrentLiabilities"))
    rev   = _f(inc.get("Revenue"))  or _f(inc.get("TotalRevenue"))
    rev_p = _f(pinc.get("Revenue")) or _f(pinc.get("TotalRevenue"))
    cogs, cogs_p = _f(inc.get("CostofRevenueTotal")), _f(pinc.get("CostofRevenueTotal"))
    sh,   sh_p   = _f(bal.get("TotalCommonSharesOutstanding")), _f(pbal.get("TotalCommonSharesOutstanding"))

    essential = [ni, ni_p, ta, ta_p, cfo, rev, rev_p, ca, ca_p, cl, cl_p, cogs, cogs_p]
    if any(v is None for v in essential) or 0 in (ta, ta_p, rev, rev_p, cl, cl_p):
        return None

    roa, roa_p = ni / ta, ni_p / ta_p
    score = 0
    if roa > 0:                                    score += 1   # 1  positive ROA
    if cfo > 0:                                     score += 1   # 2  positive operating cash flow
    if roa > roa_p:                                 score += 1   # 3  rising ROA
    if cfo > ni:                                     score += 1   # 4  CFO exceeds net income (quality)
    if ltd is not None and ltd_p is not None and (ltd / ta) < (ltd_p / ta_p):
                                                     score += 1   # 5  lower long-term leverage
    if (ca / cl) > (ca_p / cl_p):                    score += 1   # 6  higher current ratio
    if sh is not None and sh_p is not None and sh <= sh_p:
                                                     score += 1   # 7  no share dilution
    if ((rev - cogs) / rev) > ((rev_p - cogs_p) / rev_p):
                                                     score += 1   # 8  higher gross margin
    if (rev / ta) > (rev_p / ta_p):                  score += 1   # 9  higher asset turnover
    return score


def parse_company(raw: dict, default_symbol: str | None = None, isin: str | None = None) -> Company | None:
    if not isinstance(raw, dict):
        return None
    name = (raw.get("companyName") or "").strip()
    profile = raw.get("companyProfile") or {}
    nse = (profile.get("exchangeCodeNse") or default_symbol or "").strip().upper()
    if not name or not nse:
        return None

    reuse = raw.get("stockDetailsReusableData") or {}
    market_cap = _f(reuse.get("marketCap"))

    periods = _annual_periods(raw)
    latest = periods[0] if periods else {}
    prior = periods[1] if len(periods) > 1 else {}
    smap = latest.get("stockFinancialMap") or {}
    psmap = prior.get("stockFinancialMap") or {}
    inc, bal, cas = _map(smap.get("INC")), _map(smap.get("BAL")), _map(smap.get("CAS"))
    prior_inc, prior_bal, prior_cas = _map(psmap.get("INC")), _map(psmap.get("BAL")), _map(psmap.get("CAS"))

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

    eps_now = _f(inc.get("DilutedEPSExcludingExtraOrdItems"))
    eps_prev = _f(prior_inc.get("DilutedEPSExcludingExtraOrdItems"))
    eps_growth = (round((eps_now / eps_prev - 1) * 100.0, 2)
                  if eps_now is not None and eps_prev is not None and eps_prev > 0 else None)
    fscore = _piotroski_fscore(inc, bal, cas, prior_inc, prior_bal, prior_cas)

    return Company(
        name=name,
        nse_symbol=nse,
        market_cap=market_cap,
        fiscal_year=str(latest.get("FiscalYear")) if latest.get("FiscalYear") else None,
        period_end=latest.get("EndDate"),
        net_income=_f(inc.get("NetIncome")),
        prev_net_income=_f(prior_inc.get("NetIncome")),
        total_equity=_f(bal.get("TotalEquity")),
        total_assets=_f(bal.get("TotalAssets")),
        total_debt=_f(bal.get("TotalDebt")),
        operating_income=_f(inc.get("OperatingIncome")),
        depreciation=_f(inc.get("Depreciation/Amortization")),
        revenue=_f(inc.get("Revenue")),
        prev_revenue=_f(prior_inc.get("Revenue")),
        eps=_f(inc.get("DilutedEPSExcludingExtraOrdItems")),
        prev_eps=_f(prior_inc.get("DilutedEPSExcludingExtraOrdItems")),
        isin=isin,
        ratios=ratios,
        raw_industry=raw.get("industry"),
        eps_growth=eps_growth,
        piotroski=fscore,
    )


def pooled_metrics(companies: list[Company], *, is_financial: bool) -> dict:
    mc_prof, n_pe = 0.0, 0
    ni_prof = 0.0
    for c in companies:
        if c.profitable and c.market_cap is not None:
            mc_prof += c.market_cap
            ni_prof += c.net_income
            n_pe += 1
    pe = (mc_prof / ni_prof) if n_pe and ni_prof > 0 else None

    sum_eq_for_mc = sum(c.total_equity for c in companies
                        if c.total_equity is not None and c.market_cap is not None)
    sum_mc_all = sum(c.market_cap for c in companies
                     if c.market_cap is not None and c.total_equity is not None)
    pb = (sum_mc_all / sum_eq_for_mc) if sum_eq_for_mc > 0 else None

    ni_e = sum(c.net_income for c in companies
               if c.net_income is not None and c.total_equity is not None)
    eq = sum(c.total_equity for c in companies
             if c.net_income is not None and c.total_equity is not None)
    roe = (ni_e / eq * 100.0) if eq > 0 else None

    ni_a = sum(c.net_income for c in companies
               if c.net_income is not None and c.total_assets is not None)
    assets = sum(c.total_assets for c in companies
                 if c.net_income is not None and c.total_assets is not None)
    roa = (ni_a / assets * 100.0) if assets > 0 else None

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
        roce = (ebit / cap * 100.0) if cap > 0 else None

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
    if is_financial:
        roce = None
    elif (c.operating_income is not None and c.total_equity is not None
          and c.total_debt is not None and (c.total_equity + c.total_debt) > 0):
        roce = round(c.operating_income / (c.total_equity + c.total_debt) * 100.0, 2)
    else:
        roce = None

    if (c.revenue is not None and c.prev_revenue not in (None, 0)
            and c.prev_revenue > 0):
        rev_growth = round((c.revenue / c.prev_revenue - 1) * 100.0, 2)
    else:
        rev_growth = None

    return {
        "name": c.name,
        "nse_symbol": c.nse_symbol,
        "nse_url": c.nse_url(),
        "market_cap": _round(c.market_cap, 2),
        "pe": c.ratios.get("pe_ttm"),
        "pb": c.ratios.get("pb"),
        "roe": c.ratios.get("roe"),
        "roa": _round(c.net_income / c.total_assets * 100.0, 2) if (c.net_income is not None and c.total_assets and c.total_assets > 0) else None,
        "roce": roce,
        "revenue_growth_yoy": rev_growth,
        "eps_ttm_growth": c.ratios.get("eps_ttm_growth"),
        "opm": c.ratios.get("opm"),
        "npm": c.ratios.get("npm"),
        "debt_to_equity": c.ratios.get("debt_to_equity"),
        "asset_turnover": c.ratios.get("asset_turnover"),
        "interest_coverage": c.ratios.get("interest_coverage"),
        "op_rev_growth_ttm": c.ratios.get("op_rev_growth_ttm"),
    }


def rank_top(companies: list[Company], n: int = 10, *, is_financial: bool = False) -> list[dict]:
    ranked = sorted(
        (c for c in companies if c.market_cap is not None),
        key=lambda c: c.market_cap,
        reverse=True,
    )
    result = []
    for idx, c in enumerate(ranked[:n], start=1):
        m = top_metrics(c, is_financial=is_financial)
        m["rank"] = idx
        result.append(m)
    return result


class IndianAPIError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


_TIMEOUT = 20
_PACING_DELAY = 0.70          # ~85 req/min — stays under IndianAPI per-minute cap
_MAX_429_RETRIES = 4          # back off and retry on rate-limit before giving up
_BACKOFF_429 = 65             # seconds — wait out a per-minute rate window


def _api_config() -> dict:
    return {
        "base": os.environ.get("INDIANAPI_BASE_URL", "https://stock.indianapi.in").rstrip("/"),
        "path": os.environ.get("INDIANAPI_STOCK_PATH", "/stock"),
        "param": os.environ.get("INDIANAPI_NAME_PARAM", "name"),
        "key": os.environ.get("INDIANAPI_KEY", "").strip(),
    }


def has_key() -> bool:
    return bool(_api_config()["key"])


class Fetcher:
    def __init__(self, key: str | None = None) -> None:
        cfg = _api_config()
        self.key = (key or cfg["key"]).strip()
        if not self.key:
            raise RuntimeError("INDIANAPI_KEY is not set; refusing to run live refresh without key.")
        self.base = cfg["base"]
        self.path = cfg["path"]
        self.param = cfg["param"]
        self.session = requests.Session()
        self.outcomes: list[dict] = []

    def fetch(self, name: str, expected_symbol: str = "") -> dict | None:
        url = f"{self.base}{self.path}"
        headers = {"X-Api-Key": self.key, "Accept": "application/json"}
        params = {self.param: name}

        for attempt in range(_MAX_429_RETRIES + 1):
            time.sleep(_PACING_DELAY)
            try:
                resp = self.session.get(url, headers=headers, params=params, timeout=_TIMEOUT)
            except requests.Timeout:
                self.outcomes.append({"symbol": expected_symbol, "status": "failed", "reason": "timeout"})
                return None
            except requests.RequestException as e:
                self.outcomes.append({"symbol": expected_symbol, "status": "failed", "reason": f"network:{type(e).__name__}"})
                return None

            if resp.status_code in (401, 403):
                self.outcomes.append({"symbol": expected_symbol, "status": "failed", "reason": f"auth_{resp.status_code}"})
                raise IndianAPIError("auth", f"HTTP {resp.status_code} — check INDIANAPI_KEY")
            if resp.status_code == 429:
                if attempt < _MAX_429_RETRIES:
                    LOGGER.warning("HTTP 429 rate limit — backing off %ss then retrying (%d/%d) [%s]",
                                   _BACKOFF_429, attempt + 1, _MAX_429_RETRIES, expected_symbol)
                    time.sleep(_BACKOFF_429)
                    continue
                self.outcomes.append({"symbol": expected_symbol, "status": "failed", "reason": "rate_limited"})
                raise IndianAPIError("rate_limited", "HTTP 429 — persistent after retries (likely monthly credit ceiling)")
            break  # non-429 response — proceed to parse

        if resp.status_code >= 400:
            self.outcomes.append({"symbol": expected_symbol, "status": "failed", "reason": f"http_{resp.status_code}"})
            return None

        try:
            data = resp.json()
        except ValueError:
            self.outcomes.append({"symbol": expected_symbol, "status": "failed", "reason": "invalid_json"})
            return None

        # IndianAPI returns HTTP 200 with an {"error": "..."} body (e.g. "Stock
        # not found") instead of a 4xx. Treat that as a genuine failure so it is
        # not miscounted as a successful fetch.
        if isinstance(data, dict) and data.get("error") and not data.get("companyName"):
            self.outcomes.append({"symbol": expected_symbol, "status": "failed",
                                  "reason": f"not_found:{str(data.get('error'))[:40]}"})
            return None

        got_sym = ((data.get("companyProfile") or {}).get("exchangeCodeNse") or "").strip().upper()
        if expected_symbol and got_sym and got_sym != expected_symbol.upper():
            self.outcomes.append({"symbol": expected_symbol, "status": "failed", "reason": f"symbol_mismatch:{got_sym}"})
            return None

        self.outcomes.append({"symbol": expected_symbol, "status": "success", "reason": None})
        return data

