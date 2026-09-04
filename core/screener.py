"""
screener.py
-----------
Fetch the last traded price and market cap for one company straight from
Screener.in — the public company page, no third-party data API.

How it works:
1. Resolve the company name to its Screener page URL via Screener's public
   autocomplete endpoint (/api/company/search/), which maps a name to the
   right /company/<SYMBOL>/ page.
2. Download that page and read the two figures out of the "top ratios" block
   at the head of every company page ("Current Price" and "Market Cap").

Everything is best-effort: any failure (network blocked, page layout changed,
company not found) returns None so the caller falls back to the workbook's own
figures instead of breaking. Values are point-in-time; cache them for a day
upstream so this hits Screener at most once per company per day.
"""

from __future__ import annotations

import logging
import re

import requests

LOGGER = logging.getLogger("fundacheck.screener")

_BASE = "https://www.screener.in"
_TIMEOUT = 20
# A normal browser User-Agent; Screener rejects the bare python-requests default.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _num(text: str | None) -> float | None:
    """Parse a Screener figure like '3,15,760' or '3,009.50' into a float."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
    if not cleaned or cleaned == ".":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    return s


def resolve_url(name: str, session: requests.Session) -> str | None:
    """Map a company name to its Screener page path via the search endpoint."""
    try:
        resp = session.get(
            f"{_BASE}/api/company/search/",
            params={"q": name, "v": "3", "fts": "1"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        LOGGER.info("screener search failed: %s", type(exc).__name__)
        return None
    if resp.status_code != 200:
        LOGGER.info("screener search HTTP %s", resp.status_code)
        return None
    try:
        results = resp.json()
    except ValueError:
        return None
    if not isinstance(results, list) or not results:
        return None

    # Prefer an exact (case-insensitive) name match, else the first hit.
    want = name.strip().lower()
    best = next(
        (r for r in results if isinstance(r, dict)
         and str(r.get("name", "")).strip().lower() == want),
        None,
    ) or next((r for r in results if isinstance(r, dict) and r.get("url")), None)
    if not best:
        return None
    url = str(best.get("url") or "")
    if not url:
        return None
    return url if url.startswith("http") else _BASE + url


def _ratio(html: str, label: str) -> float | None:
    """
    Read one figure out of the top-ratios list, matched by its label
    ("Current Price", "Market Cap"). The value sits in the next
    <span class="number">…</span> after the label span.
    """
    pattern = re.compile(
        r'class="name">\s*' + re.escape(label) + r'\s*</span>.*?'
        r'class="number">\s*([\d.,]+)',
        re.S | re.I,
    )
    m = pattern.search(html)
    return _num(m.group(1)) if m else None


def fetch_quote(name: str, page_url: str | None = None) -> dict | None:
    """
    Live daily quote for one company from Screener.in: last traded price (₹) and
    market cap (₹ crore). Returns None on any failure.

    `page_url` may be a known /company/<SYMBOL>/ path to skip the name lookup.
    """
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
        LOGGER.info("screener page HTTP %s for %s", resp.status_code, url)
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
