"""
indianapi.py
------------
Thin client for IndianAPI (indianapi.in) — the ``/stock`` endpoint — plus a
label-tolerant extractor that pulls the raw fields the Sector lens needs from
one company's response.

Only raw figures are read here; ratios are computed later in sector_aggregate.py.
Nothing is ever invented: a field that cannot be located is left as ``None`` and
named in ``Company.missing``.

Key safety: the API key is passed in by the caller (which reads it from the
INDIANAPI_KEY environment variable). It is never logged, never written to the
snapshot, and never committed. This module is used only by the offline/CI
snapshot builder — the Streamlit app never calls it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import requests

BASE_URL = "https://stock.indianapi.in"
TIMEOUT = 30

# Label synonyms for each raw field, matched case-insensitively against the
# "key" of {key, value} statement rows (and against plain dict keys). Ordered
# most-specific first. Verified/extended against the real HDFC response.
INCOME_LABELS = {
    "net_income": ("net income", "net profit", "profit after tax", "pat",
                   "profit for the period", "profit/loss for the period",
                   "net income available to common", "consolidated net profit"),
    "ebitda": ("ebitda", "ebitd", "operating profit before dep",
               "earnings before interest tax dep"),
}
BALANCE_LABELS = {
    "equity": ("total equity", "total shareholders funds", "total shareholder funds",
               "shareholders funds", "total stockholder equity", "net worth",
               "total shareholders' funds"),
    "total_assets": ("total assets",),
    "total_debt": ("total debt", "total borrowings", "borrowings",
                   "long term debt", "long term borrowings"),
}
CASHFLOW_LABELS = {
    "dep_amort": ("depreciation & amortization", "depreciation and amortization",
                  "depreciation/depletion", "depreciation and amortisation",
                  "depreciation & amortisation", "depreciation", "depreciation/ depletion"),
}
# Market cap can appear outside the statements (keyMetrics / profile / top level).
MARKETCAP_LABELS = ("market cap", "marketcap", "mcap", "market capitalization",
                    "market capitalisation")

_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


class IndianAPIError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


@dataclass
class Company:
    symbol: str
    isin: str
    name: str
    period_label: str | None = None
    net_income: float | None = None
    net_income_prior: float | None = None      # prior annual period, for growth/PEG
    ebitda: float | None = None
    dep_amort: float | None = None
    equity: float | None = None
    total_assets: float | None = None
    total_debt: float | None = None
    market_cap: float | None = None
    missing: list[str] = field(default_factory=list)

    def ebit(self) -> float | None:
        """EBIT = EBITDA - D&A, only when both are present (same fiscal period)."""
        if self.ebitda is None or self.dep_amort is None:
            return None
        return self.ebitda - abs(self.dep_amort)

    def capital_employed(self) -> float | None:
        """Capital Employed = Total Equity + Total Debt (both required)."""
        if self.equity is None or self.total_debt is None:
            return None
        return self.equity + self.total_debt


def _to_float(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    m = _NUM.search(value.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _rows(stmt) -> list[dict]:
    return [r for r in stmt if isinstance(r, dict)] if isinstance(stmt, list) else []


def _match(rows: list[dict], synonyms: tuple[str, ...]) -> float | None:
    """First row whose 'key' contains any synonym → its numeric 'value'."""
    for syn in synonyms:                       # most-specific first
        for r in rows:
            key = str(r.get("key", "")).lower()
            if syn in key:
                v = _to_float(r.get("value"))
                if v is not None:
                    return v
    return None


def _deep_find(obj, synonyms: tuple[str, ...]) -> float | None:
    """Value-aware recursive search for a label anywhere in the payload."""
    if isinstance(obj, dict):
        name = next((obj[k] for k in obj if k.lower() in ("key", "name", "title")
                     and isinstance(obj[k], str)), None)
        val = next((obj[k] for k in obj if k.lower() in ("value", "val")
                    and isinstance(obj[k], (int, float, str))), None)
        if name and val is not None and any(s in name.lower() for s in synonyms):
            f = _to_float(val)
            if f is not None:
                return f
        for k, v in obj.items():
            if isinstance(v, (int, float, str)) and any(s in k.lower() for s in synonyms):
                f = _to_float(v)
                if f is not None:
                    return f
            r = _deep_find(v, synonyms)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _deep_find(item, synonyms)
            if r is not None:
                return r
    return None


def _annual_sorted(financials) -> list[dict]:
    """Annual periods oldest→newest. Never mixes TTM with annual.

    Each returned period's statement figures are read as one coherent set; the
    last element is the latest year, the second-to-last the prior year.
    """
    if not isinstance(financials, list):
        return []
    annual = []
    for f in financials:
        if not isinstance(f, dict):
            continue
        typ = str(f.get("Type") or f.get("type") or "").lower()
        if "annual" in typ or (not typ and f.get("stockFinancialMap")):
            annual.append(f)
    if not annual:
        annual = [f for f in financials if isinstance(f, dict) and f.get("stockFinancialMap")]

    def endkey(f):
        return str(f.get("EndDate") or f.get("endDate") or f.get("FiscalYear")
                   or f.get("fiscalYear") or f.get("Type") or "")
    annual.sort(key=endkey)
    return annual


def fetch_stock(query: str, key: str) -> dict:
    """One GET /stock?name=<query>. Raises IndianAPIError on any failure."""
    try:
        r = requests.get(f"{BASE_URL}/stock", params={"name": query},
                         headers={"X-Api-Key": key, "Accept": "application/json"},
                         timeout=TIMEOUT)
    except requests.Timeout as e:
        raise IndianAPIError("timeout") from e
    except requests.RequestException as e:
        raise IndianAPIError("network", type(e).__name__) from e
    if r.status_code == 429:
        raise IndianAPIError("rate_limited", "HTTP 429 (out of credits/rate)")
    if r.status_code in (401, 403):
        raise IndianAPIError("auth", f"HTTP {r.status_code}")
    if r.status_code >= 400:
        raise IndianAPIError("http", f"HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise IndianAPIError("parse", "non-JSON body") from e


def extract_company(raw: dict, symbol: str, isin: str, name: str) -> Company:
    """Pull the raw fields from one /stock response into a Company record."""
    c = Company(symbol=symbol, isin=isin, name=name)
    periods = _annual_sorted(raw.get("financials"))
    period = periods[-1] if periods else None
    sfm = (period or {}).get("stockFinancialMap", {}) if isinstance(period, dict) else {}
    inc, bal, cas = _rows(sfm.get("INC")), _rows(sfm.get("BAL")), _rows(sfm.get("CAS"))
    c.period_label = (str(period.get("EndDate") or period.get("FiscalYear") or "")
                      if isinstance(period, dict) else None)

    # Prior annual period's net income (for pooled earnings growth / PEG). Same
    # /stock response, no extra API call; None when only one year is available.
    if len(periods) >= 2:
        prior_sfm = periods[-2].get("stockFinancialMap", {}) if isinstance(periods[-2], dict) else {}
        c.net_income_prior = _match(_rows(prior_sfm.get("INC")), INCOME_LABELS["net_income"])

    c.net_income = _match(inc, INCOME_LABELS["net_income"])
    c.ebitda = _match(inc, INCOME_LABELS["ebitda"])
    c.dep_amort = _match(cas, CASHFLOW_LABELS["dep_amort"]) or _match(inc, CASHFLOW_LABELS["dep_amort"])
    c.equity = _match(bal, BALANCE_LABELS["equity"])
    c.total_assets = _match(bal, BALANCE_LABELS["total_assets"])
    c.total_debt = _match(bal, BALANCE_LABELS["total_debt"])
    c.market_cap = _deep_find(raw, MARKETCAP_LABELS)

    for fld in ("net_income", "ebitda", "dep_amort", "equity",
                "total_assets", "total_debt", "market_cap"):
        if getattr(c, fld) is None:
            c.missing.append(fld)
    return c


def get_company(symbol: str, isin: str, name: str, key: str) -> Company:
    """Fetch + extract one company. One API call. Raises IndianAPIError on fetch failure."""
    # Query by company name first (IndianAPI matches names); fall back to symbol.
    last: IndianAPIError | None = None
    for q in (name, symbol):
        if not q:
            continue
        try:
            raw = fetch_stock(q, key)
            return extract_company(raw, symbol, isin, name)
        except IndianAPIError as e:
            last = e
            if e.kind in ("rate_limited", "auth"):
                raise            # don't burn more calls on a hard stop
    raise last or IndianAPIError("network", "no query succeeded")
