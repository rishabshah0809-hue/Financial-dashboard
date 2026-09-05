"""
screener.py
-----------
Daily company snapshot from the PUBLIC Screener.in company page (personal,
non-commercial use). Reads only public sections; never logs in.

All numbers are parsed and every sector aggregate is computed HERE in Python —
no AI is involved in any arithmetic. Missing/invalid fields stay None and are
never coerced to zero or guessed.

Per company we read:
  top-ratios box:   Market Cap, CMP, Stock P/E, Book Value, ROE, ROCE, Div Yield
  #profit-loss:     Net Profit (TTM), Operating Profit (TTM), Depreciation (TTM)
  #balance-sheet:   Equity Capital, Reserves, Borrowings  (latest reported year)

True bottom-up aggregates (reported figures, not ratio-implied):
  Equity        = Equity Capital + Reserves           (latest annual)
  EBIT          = Operating Profit(TTM) - Depreciation(TTM)
  Capital Empl. = Equity + Borrowings
  Sector P/E    = Σ MarketCap / Σ NetProfit(TTM>0)
  Sector P/B    = Σ MarketCap / Σ Equity
  Sector ROE    = Σ NetProfit(TTM) / Σ Equity
  Sector ROCE   = Σ EBIT / Σ CapitalEmployed   (fallback: mcap-weighted reported ROCE, labelled)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

LOGGER = logging.getLogger("fundacheck.screener")

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}
_TIMEOUT = 25
_PACING = 1.2          # polite delay between requests (used by the daily runner)


def _num(raw: str | None) -> float | None:
    """Parse a Screener value ('₹17,62,680Cr.', '8.91%', '-1,234', '') -> float|None."""
    if raw is None:
        return None
    s = raw.replace(",", "").replace("₹", "").replace("%", "")
    s = re.sub(r"Cr\.?", "", s, flags=re.IGNORECASE).strip()
    if s == "" or s.lower() in ("nan", "none", "null", "-", "—", "na", "n/a"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v == v else None


def _parse_table(soup: BeautifulSoup, sec_id: str) -> dict:
    """Return {'heads': [...], 'rows': {label_lower: [floats]}} for a Screener table."""
    sec = soup.select_one(f"#{sec_id}")
    tbl = sec.select_one("table") if sec else None
    if not tbl:
        return {"heads": [], "rows": {}}
    heads = [th.get_text(strip=True) for th in tbl.select("thead th")]
    rows: dict[str, list] = {}
    for tr in tbl.select("tbody tr"):
        tds = tr.select("td")
        if not tds:
            continue
        label = tds[0].get_text(" ", strip=True).replace("+", "").strip().lower()
        rows[label] = [_num(td.get_text(strip=True)) for td in tds[1:]]
    return {"heads": heads, "rows": rows}


def _last(vals: list | None) -> float | None:
    if not vals:
        return None
    for v in reversed(vals):
        if v is not None:
            return v
    return None


@dataclass
class ScreenerCompany:
    symbol: str
    name: str
    cmp: float | None
    market_cap: float | None                 # ₹ crore
    pe_display: float | None                  # Screener "Stock P/E" (context only)
    book_value: float | None                  # ₹ per share (context only)
    roe_reported: float | None                # % (context only)
    roce_reported: float | None               # % (used only as ROCE fallback)
    dividend_yield: float | None = None
    # reported figures for TRUE aggregates (₹ crore)
    net_profit_ttm: float | None = None
    operating_profit_ttm: float | None = None
    depreciation_ttm: float | None = None
    equity_reported: float | None = None      # Equity Capital + Reserves (latest)
    borrowings: float | None = None
    sales_ttm: float | None = None            # Sales/Revenue (TTM) ₹ crore
    interest_ttm: float | None = None         # Interest expense (TTM) ₹ crore
    total_assets: float | None = None         # Balance-sheet Total (latest) ₹ crore
    revenue_growth: float | None = None       # YoY %, latest annual vs prior
    eps_growth: float | None = None           # YoY %, latest annual vs prior
    fundamental_period: str | None = None     # e.g. "Mar 2026 (annual) + TTM P&L"
    source_url: str = ""

    @property
    def pb_display(self) -> float | None:
        if self.cmp and self.book_value and self.book_value > 0:
            return round(self.cmp / self.book_value, 2)
        return None

    @property
    def ebit(self) -> float | None:
        if self.operating_profit_ttm is not None and self.depreciation_ttm is not None:
            return round(self.operating_profit_ttm - self.depreciation_ttm, 2)
        return None

    @property
    def opm(self) -> float | None:
        if self.operating_profit_ttm is not None and self.sales_ttm:
            return round(self.operating_profit_ttm / self.sales_ttm * 100, 2)
        return None

    @property
    def npm(self) -> float | None:
        if self.net_profit_ttm is not None and self.sales_ttm:
            return round(self.net_profit_ttm / self.sales_ttm * 100, 2)
        return None

    @property
    def roa(self) -> float | None:
        if self.net_profit_ttm is not None and self.total_assets:
            return round(self.net_profit_ttm / self.total_assets * 100, 2)
        return None

    @property
    def debt_to_equity(self) -> float | None:
        if self.borrowings is not None and self.equity_reported:
            return round(self.borrowings / self.equity_reported, 2)
        return None

    @property
    def interest_coverage(self) -> float | None:
        e = self.ebit
        if e is not None and self.interest_ttm:
            return round(e / self.interest_ttm, 2)
        return None

    @property
    def capital_employed(self) -> float | None:
        if self.equity_reported is not None and self.borrowings is not None:
            return round(self.equity_reported + self.borrowings, 2)
        return None

    def nse_url(self) -> str:
        return f"https://www.nseindia.com/get-quotes/equity?symbol={self.symbol}"

    def valid(self) -> bool:
        return bool(self.name) and self.market_cap is not None and self.market_cap > 0


def parse(html: str, symbol: str) -> ScreenerCompany | None:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.select_one("h1")
    name = h1.get_text(strip=True) if h1 else ""

    box = soup.select_one("#top-ratios")
    top: dict[str, str] = {}
    if box:
        for li in box.select("li"):
            n, v = li.select_one(".name"), li.select_one(".value")
            if n and v:
                top[" ".join(n.get_text(strip=True).split()).lower()] = \
                    " ".join(v.get_text(strip=True).split())

    pl = _parse_table(soup, "profit-loss")
    bs = _parse_table(soup, "balance-sheet")

    # TTM column is the last P&L column when present, else latest annual.
    def pl_ttm(label: str) -> float | None:
        vals = pl["rows"].get(label)
        return _last(vals)
    ttm_flag = bool(pl["heads"]) and pl["heads"][-1].strip().upper() == "TTM"
    latest_annual = pl["heads"][-2] if (ttm_flag and len(pl["heads"]) >= 2) else (
        pl["heads"][-1] if pl["heads"] else None)

    eq_cap = _last(bs["rows"].get("equity capital"))
    reserves = _last(bs["rows"].get("reserves"))
    equity = (eq_cap + reserves) if (eq_cap is not None and reserves is not None) else None

    # Balance-sheet total assets — for ROA. Screener labels it "Total Assets"
    # (older layouts used a bare "Total" that got overwritten by the assets row).
    total_assets = _last(bs["rows"].get("total assets") or bs["rows"].get("total"))

    # Sales/Revenue and Interest (TTM) for margins and interest cover.
    sales_row = pl["rows"].get("sales") or pl["rows"].get("revenue")
    sales_ttm = _last(sales_row)
    interest_ttm = _last(pl["rows"].get("interest"))

    def _yoy(vals: list | None) -> float | None:
        """YoY % from the annual series (drop the TTM column if present)."""
        if not vals:
            return None
        annual = vals[:-1] if ttm_flag else vals
        a = [v for v in annual if v is not None]
        if len(a) < 2 or not a[-2]:
            return None
        return round((a[-1] - a[-2]) / abs(a[-2]) * 100, 2)

    revenue_growth = _yoy(sales_row)
    eps_growth = _yoy(pl["rows"].get("eps in rs") or pl["rows"].get("eps"))

    comp = ScreenerCompany(
        symbol=symbol.upper(),
        name=name,
        cmp=_num(top.get("current price")),
        market_cap=_num(top.get("market cap")),
        pe_display=_num(top.get("stock p/e")),
        book_value=_num(top.get("book value")),
        roe_reported=_num(top.get("roe")),
        roce_reported=_num(top.get("roce")),
        dividend_yield=_num(top.get("dividend yield")),
        net_profit_ttm=pl_ttm("net profit"),
        operating_profit_ttm=pl_ttm("operating profit"),
        depreciation_ttm=pl_ttm("depreciation"),
        equity_reported=equity,
        borrowings=_last(bs["rows"].get("borrowings")),
        sales_ttm=sales_ttm,
        interest_ttm=interest_ttm,
        total_assets=total_assets,
        revenue_growth=revenue_growth,
        eps_growth=eps_growth,
        fundamental_period=(f"{latest_annual} (annual)" + (" + TTM P&L" if ttm_flag else "")
                            if latest_annual else None),
        source_url=f"https://www.screener.in/company/{symbol.upper()}/",
    )
    return comp if comp.valid() else None


def fetch(symbol: str, session: requests.Session | None = None) -> ScreenerCompany | None:
    """Fetch one company (consolidated first, then standalone). Never raises for a
    single company so the daily runner can preserve prior data on failure."""
    sess = session or requests.Session()
    sym = symbol.strip().upper()
    for cons in ("consolidated/", ""):
        try:
            r = sess.get(f"https://www.screener.in/company/{sym}/{cons}",
                         headers=_HEADERS, timeout=_TIMEOUT)
        except requests.RequestException as e:
            LOGGER.warning("screener network error %s: %s", sym, type(e).__name__)
            return None
        if r.status_code == 200 and r.text:
            comp = parse(r.text, sym)
            if comp:
                return comp
        elif r.status_code in (429, 403):
            LOGGER.warning("screener blocked/limited (%s) for %s", r.status_code, sym)
            return None
    return None


# --------------------------------------------------------------------------
# Deterministic bottom-up sector aggregation (Python only — no AI arithmetic)
# --------------------------------------------------------------------------

def pooled_metrics(companies: list[ScreenerCompany], *, is_financial: bool = False) -> dict:
    """True bottom-up sector metrics from reported constituent figures."""
    def _agg(pred_num, pred_den):
        num = den = 0.0
        incl, excl = [], {}
        for c in companies:
            n, d = pred_num(c), pred_den(c)
            if n is not None and d is not None and d > 0:
                num += n
                den += d
                incl.append(c.symbol)
            else:
                excl[c.symbol] = "missing/invalid inputs"
        return (num, den, incl, excl)

    # P/E = Σ MarketCap / Σ NetProfit(TTM > 0)
    mc_pe = np_pe = 0.0
    incl_pe, excl_pe = [], {}
    for c in companies:
        if c.net_profit_ttm is not None and c.net_profit_ttm > 0 and c.market_cap:
            mc_pe += c.market_cap
            np_pe += c.net_profit_ttm
            incl_pe.append(c.symbol)
        else:
            excl_pe[c.symbol] = ("no market cap" if not c.market_cap else
                                 "net profit <=0 or unavailable")
    pe = round(mc_pe / np_pe, 2) if np_pe > 0 else None

    # P/B = Σ MarketCap / Σ Equity
    mc_pb, eq_pb, incl_pb, _ = _agg(lambda c: c.market_cap, lambda c: c.equity_reported)
    pb = round(mc_pb / eq_pb, 2) if eq_pb > 0 else None

    # ROE = Σ NetProfit(TTM) / Σ Equity  (matched set; NetProfit may be negative)
    ni_roe = eq_roe = 0.0
    incl_roe = []
    for c in companies:
        if c.net_profit_ttm is not None and c.equity_reported is not None and c.equity_reported > 0:
            ni_roe += c.net_profit_ttm
            eq_roe += c.equity_reported
            incl_roe.append(c.symbol)
    roe = round(ni_roe / eq_roe * 100.0, 2) if eq_roe > 0 else None

    # ROCE
    roce = None
    incl_roce = []
    if is_financial:
        roce_method = "not applicable (financial sector)"
    else:
        ebit_sum = ce_sum = 0.0
        for c in companies:
            if c.ebit is not None and c.capital_employed is not None and c.capital_employed > 0:
                ebit_sum += c.ebit
                ce_sum += c.capital_employed
                incl_roce.append(c.symbol)
        if ce_sum > 0:
            roce = round(ebit_sum / ce_sum * 100.0, 2)
            roce_method = "Σ EBIT / Σ (Equity + Borrowings); EBIT = OperatingProfit(TTM) − Depreciation(TTM)"
        else:
            # labelled fallback: mcap-weighted reported ROCE
            w = wsum = 0.0
            for c in companies:
                if c.roce_reported is not None and c.market_cap:
                    w += c.roce_reported * c.market_cap
                    wsum += c.market_cap
                    incl_roce.append(c.symbol)
            roce = round(w / wsum, 2) if wsum > 0 else None
            roce_method = "market-cap-weighted average of reported ROCE (fallback; not a true aggregate)"

    return {
        "pe": pe, "pb": pb, "roe": roe, "roce": roce,
        "methodology": {
            "pe": {"formula": "Σ MarketCap / Σ NetProfit(TTM>0)",
                   "included": len(incl_pe), "excluded": len(excl_pe), "exclusions": excl_pe},
            "pb": {"formula": "Σ MarketCap / Σ Equity (Equity = Equity Capital + Reserves)",
                   "included": len(incl_pb), "excluded": len(companies) - len(incl_pb)},
            "roe": {"formula": "Σ NetProfit(TTM) / Σ Equity",
                    "included": len(incl_roe), "excluded": len(companies) - len(incl_roe)},
            "roce": {"formula": roce_method, "included": len(incl_roce),
                     "excluded": (0 if is_financial else len(companies) - len(incl_roce))},
        },
        "companies_in_pe": len(incl_pe),
    }


def constituent_row(c: ScreenerCompany, *, is_financial: bool = False) -> dict:
    """One comparison-table row (values from Screener; missing -> None)."""
    return {
        "name": c.name,
        "nse_symbol": c.symbol,
        "nse_url": c.nse_url(),
        "screener_url": c.source_url,
        "cmp": c.cmp,
        "market_cap": c.market_cap,
        "pe": c.pe_display,
        "pb": c.pb_display,
        "roe": c.roe_reported,
        "roce": None if is_financial else c.roce_reported,
        "roa": None if is_financial else c.roa,
        "opm": c.opm,
        "npm": c.npm,
        "debt_to_equity": c.debt_to_equity,
        "interest_coverage": c.interest_coverage,
        "revenue_growth_yoy": c.revenue_growth,
        "eps_ttm_growth": c.eps_growth,
        "book_value": c.book_value,
        "dividend_yield": c.dividend_yield,
        "net_profit_ttm": c.net_profit_ttm,
        "equity": c.equity_reported,
    }


def build_constituents(companies: list[ScreenerCompany], *, is_financial: bool = False) -> list[dict]:
    ranked = sorted((c for c in companies if c.market_cap is not None),
                    key=lambda c: c.market_cap, reverse=True)
    out = []
    for i, c in enumerate(ranked, start=1):
        row = constituent_row(c, is_financial=is_financial)
        row["rank"] = i
        out.append(row)
    return out


# --- Live single-company quote (used by the company hero card) ----------------
# Kept from the remote's screener module so app.py's live Last-Traded-Price /
# Market-Cap enrichment keeps working. Distinct from the sector-aggregation
# scraper above: this resolves a name -> Screener page and reads just the two
# price figures. Reuses _num / _HEADERS / _TIMEOUT above.
_BASE = "https://www.screener.in"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    s.headers["Accept"] = "text/html,application/json,application/xhtml+xml,*/*"
    return s


def resolve_url(name: str, session: requests.Session) -> str | None:
    """Map a company name to its Screener page path via the search endpoint."""
    try:
        resp = session.get(f"{_BASE}/api/company/search/",
                           params={"q": name, "v": "3", "fts": "1"}, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        LOGGER.info("screener search failed: %s", type(exc).__name__)
        return None
    if resp.status_code != 200:
        return None
    try:
        results = resp.json()
    except ValueError:
        return None
    if not isinstance(results, list) or not results:
        return None
    want = name.strip().lower()
    best = next((r for r in results if isinstance(r, dict)
                 and str(r.get("name", "")).strip().lower() == want), None) \
        or next((r for r in results if isinstance(r, dict) and r.get("url")), None)
    if not best:
        return None
    url = str(best.get("url") or "")
    if not url:
        return None
    return url if url.startswith("http") else _BASE + url


def _ratio(html: str, label: str) -> float | None:
    """Read one figure out of the top-ratios list, matched by its label."""
    pattern = re.compile(r'class="name">\s*' + re.escape(label) + r'\s*</span>.*?'
                         r'class="number">\s*([\d.,]+)', re.S | re.I)
    m = pattern.search(html)
    return _num(m.group(1)) if m else None


def fetch_quote(name: str, page_url: str | None = None) -> dict | None:
    """Live daily quote (last price ₹, market cap ₹ cr) for one company. None on failure."""
    if not name and not page_url:
        return None
    session = _session()
    url = page_url or resolve_url(name, session)
    if url and not url.startswith("http"):
        url = _BASE + url
    if not url:
        return None
    try:
        resp = session.get(url, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        LOGGER.info("screener page fetch failed: %s", type(exc).__name__)
        return None
    if resp.status_code != 200:
        return None
    html = resp.text
    price = _ratio(html, "Current Price")
    mcap = _ratio(html, "Market Cap")
    if price is None and mcap is None:
        return None
    quote: dict[str, object] = {"source": "Screener.in", "page_url": url}
    if price is not None:
        quote["current_price"] = price
    if mcap is not None:
        quote["market_cap"] = mcap
    return quote
