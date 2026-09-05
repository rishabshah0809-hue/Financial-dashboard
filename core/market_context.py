"""
market_context.py
-----------------
The *current* market cycle for the Sector Lens — deliberately separate from the
structural seasonality in ``core.seasonality``.

What it does
============
1. Fetch a *small* set (2–4) of recent, trusted headlines — last 24–72h — via
   Google News RSS, source-scoped to Reuters, Zerodha, RBI, NSE/BSE and other
   credible financial publishers. Only titles + short summaries are used; no
   filings, annual reports or long articles are ever sent anywhere (rule §6).
2. Synthesize a compact, *sector-specific* current-cycle read (a cycle label, a
   4–5 line narrative, the volatility drivers, and a one-line current tilt) with
   the LLM already configured for the app (``core.llm``) — used, never modified.
   The LLM only interprets the supplied headlines + structural context; it is
   told not to invent events and does no arithmetic (rule §14).
3. Cache the result per sector per day in ``data/market_context_cache.json`` so
   Streamlit reruns never refetch or re-synthesize. On any failure it falls back
   to the latest cached entry (showing its date), and if there is none, to a
   deterministic read built only from the real fetched headlines + the sector's
   structural transmission text. It never fabricates a current event.

No key / no network → the section still renders (structural + whatever headlines
were retrievable), clearly dated, never blank and never invented.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import requests

from . import tilt as TILT
from .llm import LLMConfig, post

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "market_context_cache.json"

_TIMEOUT = 12
_FRESH_HOURS = 24                 # re-use today's cached entry; refetch when older
_GNEWS = ("https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en")

# Credible sources we prefer to keep when ranking headlines (rule §6 priority).
_TRUSTED = ("reuters", "zerodha", "rbi.org", "nseindia", "bseindia",
            "business-standard", "financialexpress", "livemint", "moneycontrol",
            "economictimes", "cnbctv18", "ndtvprofit", "thehindubusinessline")

# Sector-specific search phrase — makes the current-cycle read differ by sector
# (rule §10). Keyed by core.sector_universe sector key.
_SECTOR_QUERY: dict[str, str] = {
    "bank": "India banks credit growth deposit NIM RBI liquidity asset quality",
    "nbfc": "India NBFC funding cost credit growth RBI liquidity",
    "housing_finance": "India housing finance home loan rates demand",
    "insurance": "India insurance premium growth IRDAI rates",
    "financial_services": "India financial services banks NBFC RBI",
    "it": "India IT services US tech spending deal wins USD INR AI",
    "telecom": "India telecom ARPU tariff 5G subscribers",
    "media": "India media advertising OTT box office",
    "pharma": "India pharma USFDA US generics pricing exports",
    "hospitals": "India hospitals occupancy ARPOB healthcare",
    "healthcare": "India healthcare pharma hospitals diagnostics",
    "auto": "India auto sales volumes demand commodity input costs",
    "fmcg": "India FMCG rural demand volume input cost inflation",
    "consumer_durables": "India consumer durables demand input costs",
    "consumer_services": "India consumption discretionary spending travel QSR",
    "retail": "India retail consumption demand discretionary",
    "realty": "India real estate housing demand interest rates sales",
    "metal": "India metals steel prices China demand LME",
    "cement": "India cement demand prices fuel cost",
    "chemicals": "India chemicals prices feedstock China supply",
    "oil_gas": "India oil gas crude prices refining margins OMC",
    "power": "India power demand renewables capex tariffs",
    "capital_goods": "India capital goods capex order inflows engineering",
    "construction": "India construction infrastructure orders capex",
    "commercial_transport": "India logistics ports aviation freight volumes",
    "commodities": "India commodities metals oil prices global demand",
    "infrastructure": "India infrastructure capex power roads orders",
}
_MACRO_QUERY = "India stock market Nifty Sensex RBI rupee crude FII flows US Fed yields"


# ---------------------------------------------------------------------------
# fetch — Google News RSS, compact
# ---------------------------------------------------------------------------
def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()


def _fetch_rss(query: str, limit: int, when: str = "3d") -> list[dict]:
    url = _GNEWS.format(q=quote_plus(f"{query} when:{when}"))
    try:
        resp = requests.get(url, timeout=_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0 (FundaCheck market context)"})
        if resp.status_code != 200 or not resp.text:
            return []
        root = ET.fromstring(resp.text)
    except (requests.RequestException, ET.ParseError):
        return []
    items = []
    for it in root.iter("item"):
        title = _clean((it.findtext("title") or ""))
        link = (it.findtext("link") or "").strip()
        src = _clean((it.findtext("{http://news.google.com}source")
                      or it.findtext("source") or ""))
        # Google titles read "Headline - Publisher"; split the publisher off.
        if not src and " - " in title:
            title, src = title.rsplit(" - ", 1)
        pub = (it.findtext("pubDate") or "").strip()
        try:
            dt = parsedate_to_datetime(pub) if pub else None
        except (TypeError, ValueError):
            dt = None
        items.append({"title": title.strip(), "source": src.strip(),
                      "url": link, "date": dt.date().isoformat() if dt else None,
                      "_ts": dt.timestamp() if dt else 0.0})
        if len(items) >= limit * 3:
            break
    return items


def _select(macro: list[dict], sector: list[dict], want: int = 4) -> list[dict]:
    """Prefer trusted, recent items; keep a couple sector-specific + macro."""
    def trusted(it):
        blob = (it.get("url", "") + " " + it.get("source", "")).lower()
        return any(t in blob for t in _TRUSTED)

    def rank(lst):
        return sorted(lst, key=lambda i: (trusted(i), i.get("_ts", 0)), reverse=True)

    picked, seen = [], set()
    # up to 2 sector-specific, then fill from macro, deduped, capped at `want`
    for pool, cap in ((rank(sector), 2), (rank(macro), want)):
        added = 0
        for it in pool:
            if len(picked) >= want or added >= cap:
                break
            key = it.get("title", "").lower()[:80]
            if not key or key in seen:
                continue
            seen.add(key)
            picked.append({k: it[k] for k in ("title", "source", "url", "date")})
            added += 1
    return picked[:want]


# ---------------------------------------------------------------------------
# synthesis — LLM interprets the headlines; strict no-fabrication contract
# ---------------------------------------------------------------------------
_CYCLE_LABELS = ("Early Expansion", "Expansion", "Late Expansion", "Peak",
                 "Contraction", "Recovery", "Mixed / Transitional")


def _messages(sector_name: str, structural: str, headlines: list[dict]) -> list[dict]:
    heads = "\n".join(f"- [{h.get('source') or 'source'} · {h.get('date') or 'n/a'}] "
                      f"{h['title']}" for h in headlines) or "- (no recent headlines retrieved)"
    system = (
        "You are a sell-side macro strategist writing the 'Current cycle' note for "
        "one Indian equity sector. Use ONLY the headlines provided plus the "
        "structural context. Do NOT invent events, numbers, dates or prices. If the "
        "headlines do not support a directional call, use 'Mixed / Transitional'. "
        "Do no arithmetic. Keep it specific to THIS sector's transmission channel.")
    user = (
        f"Sector: {sector_name}\n"
        f"Structural context: {structural}\n\n"
        f"Recent headlines (last ~72h):\n{heads}\n\n"
        "Return STRICT JSON with keys:\n"
        f'  "label": one of {list(_CYCLE_LABELS)},\n'
        '  "lines": array of 4-5 short sentences covering, in order — global market '
        'trend; India market trend; the main transmission channel FOR THIS SECTOR; '
        'the specific current volatility drivers (name them, e.g. crude, US yields, '
        'rupee, FII flows); and what to watch next;\n'
        '  "drivers": array of 2-5 short driver tags actually supported by the '
        'headlines;\n'
        '  "tilt": one concise sentence, e.g. "Mixed — earnings supportive but higher '
        'crude and global yields cap multiple expansion".\n'
        "No text outside the JSON.")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _synthesize(config: LLMConfig | None, sector_name: str, structural: str,
                headlines: list[dict]) -> dict | None:
    live = config is not None and (config.is_live
                                   or any(c.is_live for c in getattr(config, "fallbacks", [])))
    if not live:
        return None
    try:
        raw = post(config, _messages(sector_name, structural, headlines), json_mode=True)
    except Exception:                                    # noqa: BLE001 — fail soft
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    label = data.get("label")
    lines = [str(x).strip() for x in (data.get("lines") or []) if str(x).strip()]
    if label not in _CYCLE_LABELS or len(lines) < 3:
        return None
    return {
        "label": label,
        "lines": lines[:5],
        "drivers": [str(x).strip() for x in (data.get("drivers") or [])][:5],
        "tilt": str(data.get("tilt") or "").strip(),
        "from_llm": True,
    }


def _deterministic(sector_name: str, structural: str, headlines: list[dict]) -> dict:
    """No LLM: a factual read built only from real headlines + structural text.
    Names no events that were not retrieved; makes no directional overclaim."""
    first = structural.split(". ")[0].strip()
    lines = [
        "Live macro synthesis is unavailable, so this is a structural read plus the "
        "latest retrieved headlines (shown below) rather than an AI narrative.",
        f"For {sector_name}, the core transmission channel is: {first}.",
    ]
    if headlines:
        lines.append("Recent context: "
                     + "; ".join(h["title"] for h in headlines[:2]) + ".")
    return {"label": "Mixed / Transitional", "lines": lines,
            "drivers": [], "tilt": "", "from_llm": False}


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------
def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    except OSError:
        pass


def _latest_for(cache: dict, sector_key: str) -> dict | None:
    entries = [v for k, v in cache.items() if v.get("sector_key") == sector_key]
    return max(entries, key=lambda e: e.get("retrieved_at", ""), default=None)


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------
def get_context(sector_key: str, sector_name: str, config: LLMConfig | None = None,
                *, force: bool = False) -> dict:
    """Current-cycle context for one sector. Cached per sector per day; fails
    soft to the latest cached entry (dated), then to a deterministic read."""
    cache = _load_cache()
    today = datetime.now(timezone.utc).date().isoformat()
    key = f"{sector_key}:{today}"
    if not force and key in cache:
        return cache[key]

    macro = _fetch_rss(_MACRO_QUERY, limit=4)
    sector_q = _SECTOR_QUERY.get(sector_key, f"India {sector_name} sector")
    sector_items = _fetch_rss(sector_q, limit=4)
    headlines = _select(macro, sector_items, want=4)

    structural = TILT.profile(sector_key).get("text", "")
    read = (_synthesize(config, sector_name, structural, headlines)
            if headlines or config else None)

    if read is None and not headlines:
        # nothing fresh and no synthesis -> reuse the last good entry if any
        prev = _latest_for(cache, sector_key)
        if prev:
            prev = dict(prev)
            prev["stale"] = True
            return prev
    if read is None:
        read = _deterministic(sector_name, structural, headlines)

    entry = {
        "sector_key": sector_key,
        "sector_name": sector_name,
        "news_snapshot_date": today,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "updated_display": datetime.now(timezone.utc).strftime("%d %b %Y"),
        "label": read["label"],
        "lines": read["lines"],
        "drivers": read.get("drivers", []),
        "tilt": read.get("tilt", ""),
        "from_llm": read.get("from_llm", False),
        "sources": headlines,
        "stale": False,
    }
    cache[key] = entry
    # keep the cache small: last ~60 entries
    if len(cache) > 60:
        for k in sorted(cache, key=lambda k: cache[k].get("retrieved_at", ""))[:-60]:
            cache.pop(k, None)
    _save_cache(cache)
    return entry
