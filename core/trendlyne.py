"""
trendlyne.py
------------
Fetches a live *sector-level* snapshot from Trendlyne for the Sector lens.

Given a sector's Trendlyne id/slug it downloads the public sector page
(``https://trendlyne.com/equity/sector/{id}/{slug}/``) and pulls the headline
aggregates — sector score, number of companies, average market cap, and the
sector P/E, P/B, ROE, ROCE and ROA. No API key, no login, no paid service.

Design rules, in order of importance:

* **Accuracy over completeness.** A value is only ever returned when it was
  actually read off the page. Anything that cannot be parsed reliably is left
  as ``None`` (rendered as an em dash upstream) and named in ``missing`` — the
  Lens never invents, estimates or guesses a number.
* **Fail soft, never fake.** 429 (rate limit), Cloudflare / bot challenge,
  timeouts, network errors and unparseable pages all resolve to an
  ``ok = False`` result carrying a reason, so the Lens can show a clear
  "temporarily unavailable" state and keep working from the uploaded workbook.
* **Fetch once per sector per day.** The successful fetch is memoised with
  ``st.cache_data(ttl=86400)`` so opening the dashboard does not hammer
  Trendlyne. Failures are *not* cached, so a transient block is retried later.

The parsing is deliberately layout-tolerant (label → nearest number) because
Trendlyne's markup is not a stable contract; every extracted value is
bounds-checked so a stray number can't masquerade as a ratio.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import requests

LOGGER = logging.getLogger("fundacheck.trendlyne")

BASE_URL = "https://trendlyne.com/equity/sector/{id}/{slug}/"
TIMEOUT = 12  # seconds

# A plain browser-like request. Trendlyne serves the public sector page to
# ordinary browsers; this does not attempt to defeat a bot challenge — if one is
# returned we surface it as "unavailable" rather than working around it.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Cache-Control": "no-cache",
}

# India Standard Time, for a human-readable "fetched at" with no tz dependency.
_IST = timezone(timedelta(hours=5, minutes=30))

# Optional Streamlit cache. Imported lazily so the module is usable (and
# testable) outside a Streamlit runtime — there the decorator is a no-op.
try:                                                # pragma: no cover - env dependent
    import streamlit as st

    _cache = st.cache_data(ttl=86400, show_spinner=False)
except Exception:                                   # noqa: BLE001
    def _cache(func):
        return func


class _FetchError(Exception):
    """Raised inside the cached path so a failure is never memoised."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


# --------------------------------------------------------------------------
# value parsing
# --------------------------------------------------------------------------
_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
# A number Trendlyne would print: 22.94, 19,110, 1.5, -3.2, 48.8%.
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _to_text(html: str) -> str:
    """Strip scripts/styles and tags, collapse whitespace to a flat string."""
    no_scripts = _SCRIPT_STYLE.sub(" ", html)
    text = _TAGS.sub(" ", no_scripts)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return _WS.sub(" ", text).strip()


def _num(token: str) -> float | None:
    try:
        return float(token.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _value_after(text: str, labels: tuple[str, ...], window: int = 40
                 ) -> float | None:
    """
    Find the first number that follows any of ``labels`` within ``window`` chars.

    Labels are tried in order (most specific first) and matched case-insensitively
    on a whitespace-flexible basis, so "Price to Earning Ratio" matches even when
    the page rendered it across several elements.
    """
    low = text.lower()
    for label in labels:
        # Whitespace-flexible, and guarded so a short acronym (ROA, PE, P/B) can't
        # match inside a longer word ("broad", "shape", ...).
        core = re.escape(label.lower()).replace(r"\ ", r"\s+")
        pattern = r"(?<![a-z])" + core + r"(?![a-z])"
        for m in re.finditer(pattern, low):
            segment = text[m.end():m.end() + window]
            hit = _NUM.search(segment)
            if hit:
                val = _num(hit.group(0))
                if val is not None:
                    return val
    return None


def _bounded(value: float | None, lo: float, hi: float) -> float | None:
    """Keep a parsed value only if it falls in a sane range for that metric."""
    if value is None:
        return None
    return value if lo <= value <= hi else None


def _sector_name_from_html(html: str, fallback: str) -> str:
    """Read the sector's own label off the page <title>/<h1>, else the fallback."""
    for pat in (r"<h1[^>]*>(.*?)</h1>", r"<title[^>]*>(.*?)</title>"):
        m = re.search(pat, html, re.I | re.S)
        if not m:
            continue
        raw = _to_text(m.group(1))
        # Titles read "<Sector> Stocks Sector analysis, peers and performance".
        raw = re.split(r"\bStocks\b|\bSector analysis\b|\|", raw)[0].strip(" -–—")
        if raw:
            return raw
    return fallback


# Ordered label variants for each metric (most specific first) and the sane
# range a real reading must fall inside.
_METRIC_SPEC: dict[str, tuple[tuple[str, ...], tuple[float, float]]] = {
    "sector_score": (("Sector Score",), (0.0, 100.0)),
    "companies": (("No. of Companies", "No of Companies", "Number of Companies",
                   "Number of companies"), (1.0, 100000.0)),
    "avg_market_cap": (("Avg Market Cap", "Average Market Cap", "Avg. Market Cap",
                        "Avg Mcap", "Average Mcap"), (0.0, 5.0e8)),
    "pe": (("Price to Earning Ratio", "Price to Earnings Ratio", "PE TTM",
            "PE Ratio", "P/E Ratio", "P/E"), (0.0, 1000.0)),
    "pb": (("Price to Book Ratio", "Price to Book Value", "Price to Book",
            "PB Ratio", "P/B Ratio", "P/B"), (0.0, 200.0)),
    "roe": (("Return on Equity", "ROE"), (-200.0, 300.0)),
    "roce": (("Return on Capital Employed", "ROCE"), (-200.0, 300.0)),
    "roa": (("Return on Assets", "ROA"), (-200.0, 300.0)),
}

# Human labels for the "could not fetch" note.
LABELS = {
    "sector_score": "Sector Score",
    "companies": "No. of Companies",
    "avg_market_cap": "Avg Market Cap",
    "pe": "P/E",
    "pb": "P/B",
    "roe": "ROE",
    "roce": "ROCE",
    "roa": "ROA",
}

# Metrics whose presence proves we parsed a real sector page (not a challenge/404).
_CORE = ("pe", "pb", "roe", "roce", "sector_score")


def _parse(html: str, sector_name: str, trendlyne_name: str) -> dict:
    """HTML → snapshot dict. Raises _FetchError('parse') if nothing usable."""
    text = _to_text(html)
    values: dict[str, float | None] = {}
    for key, (labels, (lo, hi)) in _METRIC_SPEC.items():
        values[key] = _bounded(_value_after(text, labels), lo, hi)
    if values.get("companies") is not None:
        values["companies"] = float(int(values["companies"]))

    if not any(values.get(k) is not None for k in _CORE):
        # The page loaded but carried none of the headline ratios — treat as a
        # parse failure so it is not cached and the Lens shows "unavailable"
        # rather than a card full of dashes.
        raise _FetchError("parse")

    missing = [LABELS[k] for k in _METRIC_SPEC if values.get(k) is None]
    now = datetime.now(_IST)
    return {
        "ok": True,
        "error": None,
        "sector": sector_name,
        "trendlyne_name": _sector_name_from_html(html, trendlyne_name or sector_name),
        "fetched_at": now.isoformat(),
        "fetched_at_display": now.strftime("%d %b %Y, %H:%M IST"),
        "missing": missing,
        **values,
    }


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------
def _download(url: str) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.Timeout as exc:                 # noqa: PERF203
        raise _FetchError("timeout") from exc
    except requests.RequestException as exc:
        raise _FetchError("network") from exc

    if resp.status_code == 429:
        raise _FetchError("rate_limited")
    if resp.status_code in (401, 403):
        raise _FetchError("blocked")
    if resp.status_code >= 400:
        raise _FetchError(f"http_{resp.status_code}")

    body = resp.text or ""
    low = body.lower()
    # Cloudflare / bot-challenge interstitials return 200 with challenge markup.
    if any(sig in low for sig in ("just a moment", "cf-chl", "cf-browser-verification",
                                  "attention required", "/cdn-cgi/challenge-platform")):
        raise _FetchError("blocked")
    return body


@_cache
def _snapshot_cached(url: str, sector_name: str, trendlyne_name: str) -> dict:
    """Download + parse. Cached for 24h; only *successful* results are memoised."""
    html = _download(url)
    return _parse(html, sector_name, trendlyne_name)


# How each failure reads to a user.
_ERROR_TEXT = {
    "unmapped": "No Trendlyne sector is mapped for this sector.",
    "rate_limited": "Trendlyne is rate-limiting requests right now (HTTP 429).",
    "blocked": "Trendlyne blocked the request (bot protection).",
    "timeout": "The request to Trendlyne timed out.",
    "network": "Could not reach Trendlyne (network error).",
    "parse": "Trendlyne's page could not be read.",
}


def _unavailable(kind: str, sector_name: str, url: str | None,
                 trendlyne_name: str) -> dict:
    return {
        "ok": False,
        "error": kind,
        "error_text": _ERROR_TEXT.get(kind, "Sector data could not be fetched."),
        "sector": sector_name,
        "trendlyne_name": trendlyne_name or sector_name,
        "url": url,
        "fetched_at": None,
        "fetched_at_display": None,
        "missing": list(LABELS.values()),
        **{k: None for k in _METRIC_SPEC},
    }


def get_sector_snapshot(sector_name: str, trendlyne_id: int | None,
                        trendlyne_slug: str = "", trendlyne_name: str = "") -> dict:
    """
    Public entry point for the Sector lens.

    Returns a dict that always contains every metric key (value or ``None``),
    an ``ok`` flag, the source ``url`` and, on success, a ``fetched_at`` stamp
    and a ``missing`` list. Never raises, never fabricates a value.
    """
    if trendlyne_id is None:
        return _unavailable("unmapped", sector_name, None, trendlyne_name)

    slug = trendlyne_slug or "sector"
    url = BASE_URL.format(id=trendlyne_id, slug=slug)
    try:
        snap = _snapshot_cached(url, sector_name, trendlyne_name)
    except _FetchError as exc:
        LOGGER.warning("Trendlyne fetch failed for %s (%s): %s",
                       sector_name, url, exc.kind)
        return _unavailable(exc.kind, sector_name, url, trendlyne_name)
    except Exception as exc:                         # noqa: BLE001 - never break the UI
        LOGGER.exception("Unexpected Trendlyne error for %s", sector_name)
        return _unavailable("network", sector_name, url, trendlyne_name)
    # The cached dict is shared; add the (non-cached-critical) url for the caller.
    snap = dict(snap)
    snap["url"] = url
    return snap
