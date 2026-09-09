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
_MAX_AGE_DAYS = 183               # never show a headline older than ~6 months
_WANT_EACH = 4                    # 4 Global + 4 India items in the cycle section
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

# Per-sector INTERNATIONAL query — used to fill the Global column with genuine
# offshore drivers (China/US/Europe demand, global commodity prices, tariffs),
# not Indian index moves. Falls back to a generic global-macro query.
_GLOBAL_QUERY: dict[str, str] = {
    "metal": "China steel demand US tariffs LME copper aluminium global mining prices",
    "commodities": "China demand US tariffs LME global commodity metals oil prices",
    "oil_gas": "OPEC crude oil prices Brent global supply US shale China demand",
    "chemicals": "China chemicals feedstock global prices US Europe demand",
    "cement": "global cement fuel petcoke coal prices China demand",
    "it": "US technology spending global IT outsourcing AI deals recession",
    "pharma": "USFDA US generic drug pricing global pharma exports",
    "auto": "global auto demand semiconductor supply China EV commodity costs",
    "fmcg": "global palm oil crude commodity prices consumer demand",
    "power": "global energy prices coal LNG renewables demand",
    "capital_goods": "global capex machinery orders US Europe China demand",
    "bank": "US Fed rate decision global banking credit conditions",
    "realty": "global interest rates property demand construction costs",
}
_GLOBAL_MACRO_QUERY = ("global markets US Fed rate decision China economy Europe "
                       "commodity prices crude oil tariffs")

# --- geography classification -------------------------------------------------
# Tokens that mark a headline as India-framed (a domestic market/company/policy
# event) vs a genuine offshore/global driver. A headline is `mixed` when both
# fire, `unknown` when neither does — and unknown is NEVER treated as global.
_INDIA_TOKENS = (
    "sensex", "nifty", "bse", "nse", "dalal street", "d-street", "rupee", "rbi",
    "sebi", "irdai", "indian stock", "india stock", "indian market", "mumbai",
    "adani", "reliance", "tata ", "fpi ", "fii", "dii", "lok sabha", "gst",
    "union budget", "indian", "domestic", "india's")
_GLOBAL_TOKENS = (
    "china", "chinese", "united states", "america", "american", " us ", "us fed",
    "fed ", "federal reserve", "europe", "european", "ecb", "eurozone", "germany",
    "japan", "opec", "lme", "comex", "brent", "wti", "tariff", "trade war", "global",
    "worldwide", "imf", "treasury yield", "dollar index", "washington", "beijing",
    "geopolitic")


def _india_company_re():
    """A single alternation of distinctive known-Indian-company aliases (reusing
    the authoritative map in core.sectors), so 'JSW Steel Q1 earnings' reads as an
    India event even without an explicit 'India' token. Short tickers are excluded
    to avoid matching fragments; matched at word boundaries."""
    global _INDIA_CO_RE
    try:
        return _INDIA_CO_RE
    except NameError:
        try:
            from .sectors import KNOWN_COMPANIES
            aliases = sorted((a for a in KNOWN_COMPANIES if " " in a or len(a) >= 5),
                             key=len, reverse=True)
            _INDIA_CO_RE = (re.compile(r"\b(?:" + "|".join(re.escape(a) for a in aliases)
                                       + r")\b") if aliases else None)
        except Exception:                                # noqa: BLE001
            _INDIA_CO_RE = None
        return _INDIA_CO_RE


def classify_geography(title: str, source: str | None = None) -> str:
    """Classify a headline's event geography from its CONTENT (not its publisher):
    'india' | 'global' | 'mixed' | 'unknown'. A Reuters byline or a mention of
    China does not by itself make an item global — both an India frame and a
    global driver present → 'mixed'; neither → 'unknown' (never auto-global)."""
    t = " " + re.sub(r"[^a-z0-9 ]", " ", (title or "").lower()) + " "
    india = any(tok in t for tok in _INDIA_TOKENS)
    if not india:
        co_re = _india_company_re()
        india = bool(co_re and co_re.search(t))          # a known Indian listed name
    glob = any(tok in t for tok in _GLOBAL_TOKENS)
    if india and glob:
        return "mixed"
    if india:
        return "india"
    if glob:
        return "global"
    return "unknown"


# --- sector relevance ---------------------------------------------------------
# A global item is only shown when it materially bears on THIS sector. Keyed by
# core.sector_universe sector key; a key with no list means "do not over-filter".
_SECTOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "metal": ("steel", "aluminium", "aluminum", "copper", "zinc", "iron ore",
              "metal", "mining", "lme", "alloy", "ferrous", "ore"),
    "commodities": ("steel", "copper", "aluminium", "metal", "crude", "oil",
                    "commodity", "mining", "lme", "coal"),
    "oil_gas": ("crude", "oil", "gas", "opec", "brent", "wti", "refining",
                "refinery", "lng", "petroleum", "diesel", "fuel", "grm"),
    "chemicals": ("chemical", "feedstock", "petrochemical", "specialty", "agrochem"),
    "cement": ("cement", "clinker", "petcoke", "construction", "infrastructure"),
    "it": ("software", "it services", "tech", "technology", "ai ", "cloud",
           "semiconductor", "chip", "outsourcing", "deal", "digital"),
    "pharma": ("pharma", "drug", "generic", "usfda", "fda", "api ", "healthcare",
               "biotech", "medicine"),
    "auto": ("auto", "vehicle", "car", "ev ", "two-wheeler", "semiconductor",
             "steel", "commodity"),
    "fmcg": ("fmcg", "consumer", "staples", "palm oil", "rural", "volume",
             "inflation", "commodity"),
    "power": ("power", "electricity", "coal", "lng", "renewable", "solar", "grid",
              "energy"),
    "capital_goods": ("capex", "machinery", "orders", "engineering", "industrial",
                      "capital goods"),
    "bank": ("bank", "credit", "deposit", "nim", "loan", "lending", "rate", "npa",
             "liquidity"),
    "realty": ("real estate", "property", "housing", "realty", "home loan",
               "mortgage", "rate"),
}


def sector_relevant(title: str, sector_key: str) -> bool:
    """Does this headline materially bear on the selected sector? Unknown sectors
    (no keyword list) are not over-filtered."""
    kws = _SECTOR_KEYWORDS.get(sector_key)
    if not kws:
        return True
    t = (title or "").lower()
    return any(k in t for k in kws)


# ---------------------------------------------------------------------------
# fetch — Google News RSS, compact
# ---------------------------------------------------------------------------
def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()


def _fresh(items: list[dict], max_age_days: int = _MAX_AGE_DAYS) -> list[dict]:
    """Drop anything older than ~6 months (or undated), newest first."""
    cutoff = time.time() - max_age_days * 86400
    kept = [it for it in items if it.get("_ts", 0) and it["_ts"] >= cutoff]
    return sorted(kept, key=lambda i: i.get("_ts", 0), reverse=True)


def _fetch_rss(query: str, limit: int, when: str = "180d") -> list[dict]:
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


def _trusted(it) -> bool:
    blob = (it.get("url", "") + " " + it.get("source", "")).lower()
    return any(t in blob for t in _TRUSTED)


def _pick(pool: list[dict], want: int, seen: set) -> list[dict]:
    """Top `want` fresh, trusted-first, deduped items as compact dicts."""
    ranked = sorted(_fresh(pool), key=lambda i: (_trusted(i), i.get("_ts", 0)),
                    reverse=True)
    out = []
    for it in ranked:
        key = it.get("title", "").lower()[:80]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({k: it.get(k) for k in ("title", "source", "url", "date")})
        if len(out) >= want:
            break
    return out


# ---------------------------------------------------------------------------
# synthesis — LLM interprets the headlines; strict no-fabrication contract
# ---------------------------------------------------------------------------
_CYCLE_LABELS = ("Early Expansion", "Expansion", "Late Expansion", "Peak",
                 "Contraction", "Recovery", "Mixed / Transitional")


def _heads_block(items: list[dict]) -> str:
    return "\n".join(
        f"  [{i}] ({h.get('source') or 'source'} · {h.get('date') or 'n/a'}) {h['title']}"
        for i, h in enumerate(items)) or "  (none retrieved)"


def _messages(sector_name: str, structural: str,
              global_heads: list[dict], india_heads: list[dict]) -> list[dict]:
    system = (
        "You are a sell-side macro strategist writing the 'Where the cycle sits now' "
        "note for one Indian equity sector. Use ONLY the headlines provided plus the "
        "structural context. Do NOT invent events, numbers, dates or prices, and do "
        "NOT invent headlines beyond those listed. Reference each headline by its "
        "index. If the headlines do not support a directional call, use "
        "'Mixed / Transitional'. Do no arithmetic. Keep everything specific to THIS "
        "sector's transmission channel.")
    user = (
        f"Sector: {sector_name}\n"
        f"Structural context: {structural}\n\n"
        f"GLOBAL / macro headlines (≤6 months):\n{_heads_block(global_heads)}\n\n"
        f"INDIA sector headlines (≤6 months):\n{_heads_block(india_heads)}\n\n"
        "Return STRICT JSON with keys:\n"
        f'  "label": one of {list(_CYCLE_LABELS)},\n'
        '  "global": array of up to 4 objects {"i": <global headline index>, '
        '"explain": a 3-4 line explanation (roughly 40-65 words) of what that '
        'development means for the global complex that drives this sector}. Cover the '
        'most relevant global headlines, one object each.\n'
        '  "india": array of up to 4 objects {"i": <india headline index>, '
        '"explain": a 3-4 line explanation (roughly 40-65 words) of the read-through '
        'to Indian producers/companies in this sector}.\n'
        '  "drivers": array of 2-5 short driver tags actually supported by the '
        'headlines (e.g. "Crude oil", "US yields", "Rupee", "FII flows");\n'
        '  "tilt": 2-3 sentences (the current MARKET tilt) — the directional read '
        'and the specific factors that support or cap it.\n'
        "No text outside the JSON.")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _merge_items(heads: list[dict], explained: list[dict]) -> list[dict]:
    """Attach each LLM explanation (by index `i`) to its real headline, keeping the
    real title/source/url/date. Explanations for missing indices are dropped; items
    with no explanation fall back to the headline title as the text."""
    by_i = {}
    for e in explained or []:
        try:
            by_i[int(e.get("i"))] = str(e.get("explain") or "").strip()
        except (TypeError, ValueError):
            continue
    out = []
    for idx, h in enumerate(heads):
        out.append({"title": h.get("title"), "source": h.get("source"),
                    "url": h.get("url"), "date": h.get("date"),
                    "explain": by_i.get(idx, "")})
    # prefer items that actually got an explanation, newest order preserved
    out.sort(key=lambda x: (x["explain"] == "",))
    return out[:_WANT_EACH]


def _synthesize(config: LLMConfig | None, sector_name: str, structural: str,
                global_heads: list[dict], india_heads: list[dict]) -> dict | None:
    live = config is not None and (config.is_live
                                   or any(c.is_live for c in getattr(config, "fallbacks", [])))
    if not live:
        return None
    try:
        raw = post(config, _messages(sector_name, structural, global_heads, india_heads),
                   json_mode=True)
    except Exception:                                    # noqa: BLE001 — fail soft
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    label = data.get("label")
    if label not in _CYCLE_LABELS:
        return None
    g_items = _merge_items(global_heads, data.get("global") or [])
    i_items = _merge_items(india_heads, data.get("india") or [])
    if not g_items and not i_items:
        return None
    lines = [x["explain"] for x in (g_items + i_items) if x.get("explain")]
    return {
        "label": label,
        "global_items": g_items,
        "india_items": i_items,
        "lines": lines[:5],                              # legacy consumers
        "drivers": [str(x).strip() for x in (data.get("drivers") or [])][:5],
        "tilt": str(data.get("tilt") or "").strip(),
        "from_llm": True,
    }


def _deterministic(sector_name: str, structural: str,
                   global_heads: list[dict], india_heads: list[dict],
                   fundamentals: dict | None = None) -> dict:
    """No LLM: a factual read built ONLY from real headlines + the sector's own
    structural profile + the passed-in snapshot fundamentals. Never invents an
    event and never exposes which model/API was unavailable (rule §1/§6/§7).

    - Level B (this function, when there is any headline or fundamental context):
      a concise deterministic synthesis.
    - Level C (no headlines AND no usable fundamentals): the neutral
      no-current-signal message.
    """
    first = (structural.split(". ")[0].strip() or f"the {sector_name} cycle")
    lower_first = first[:1].lower() + first[1:] if first else ""
    g_items = [{"title": h["title"], "source": h.get("source"), "url": h.get("url"),
                "date": h.get("date"),
                "explain": (f"An international development bearing on the {sector_name} "
                            f"complex. It matters because {lower_first}, so moves in the "
                            "global market feed through to the sector before domestic "
                            "volumes react.")}
               for h in global_heads[:_WANT_EACH]]
    i_items = [{"title": h["title"], "source": h.get("source"), "url": h.get("url"),
                "date": h.get("date"),
                "explain": (f"The read-through to Indian {sector_name} producers: "
                            "domestic demand, policy and the rupee shape how this reaches "
                            "reported earnings.")}
               for h in india_heads[:_WANT_EACH]]

    f = fundamentals or {}
    eg = f.get("earnings_growth")
    tilt_state = (f.get("current_tilt") or "").strip()
    has_headlines = bool(global_heads or india_heads)
    has_fund = isinstance(eg, (int, float)) or bool(tilt_state)

    if not has_headlines and not has_fund:
        # Level C — genuinely nothing usable.
        tilt = ("No current macro signal is available from the retrieved sources. "
                "The structural sector view is shown below.")
        return {"label": "Mixed / Transitional", "global_items": [], "india_items": [],
                "lines": [first], "drivers": [], "tilt": tilt, "from_llm": False}

    # Level B — deterministic synthesis from real data only.
    bits = []
    if isinstance(eg, (int, float)):
        trend = ("contracting" if eg < -0.05 else "expanding" if eg > 0.05 else "roughly flat")
        bits.append(f"Sector earnings growth is {trend} at {eg:+.1f}% year on year")
    if tilt_state:
        bits.append(f"the fundamental read is {tilt_state.lower()}")
    lead = ("; ".join(bits).capitalize() + "." if bits else
            f"{sector_name} tracks its structural cycle.")
    tail = ("The headlines below are the latest retrieved market context for the sector."
            if has_headlines else
            "No fresh sector headlines were retrieved; the structural view is shown below.")
    return {"label": "Mixed / Transitional",
            "global_items": g_items, "india_items": i_items,
            "lines": [first], "drivers": [],
            "tilt": f"{lead} {tail}", "from_llm": False}


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
def _route(pool: list[dict], sector_key: str) -> tuple[list[dict], list[dict]]:
    """Split a fetched pool into (global_heads, india_heads) by real geography +
    sector relevance. Global = genuinely offshore drivers that bear on the
    sector; India = domestic (india/mixed/relevant-unknown). Sensex/Nifty items
    are India-framed and therefore can never land in the Global column."""
    seen: set = set()
    global_pool, india_pool = [], []
    for it in _fresh(pool):
        title = it.get("title", "")
        key = title.lower()[:80]
        if not key or key in seen:
            continue
        seen.add(key)
        geo = classify_geography(title, it.get("source"))
        relevant = sector_relevant(title, sector_key)
        if geo == "global" and relevant:
            global_pool.append(it)
        elif geo in ("india", "mixed") or (geo == "unknown" and relevant):
            india_pool.append(it)
        # global-but-irrelevant, or unknown-and-irrelevant: dropped (rule §4)
    rank = lambda lst: sorted(lst, key=lambda i: (_trusted(i), i.get("_ts", 0)),  # noqa: E731
                              reverse=True)[:_WANT_EACH]
    compact = lambda lst: [{k: it.get(k) for k in ("title", "source", "url", "date")}  # noqa: E731
                           for it in lst]
    return compact(rank(global_pool)), compact(rank(india_pool))


def get_context(sector_key: str, sector_name: str, config: LLMConfig | None = None,
                *, fundamentals: dict | None = None, force: bool = False) -> dict:
    """Current-cycle context for one sector. Cached per sector per day; fails
    soft to the latest cached entry (dated), then to a deterministic read built
    only from real headlines + the passed-in sector fundamentals."""
    cache = _load_cache()
    today = datetime.now(timezone.utc).date().isoformat()
    key = f"{sector_key}:{today}"
    if not force and key in cache:
        return cache[key]

    # Fetch international + domestic pools separately, then classify every item by
    # its actual event geography (a query is only a seed, not the label).
    global_q = _GLOBAL_QUERY.get(sector_key, _GLOBAL_MACRO_QUERY)
    india_q = _SECTOR_QUERY.get(sector_key, f"India {sector_name} sector")
    pool = (_fetch_rss(global_q, limit=_WANT_EACH)
            + _fetch_rss(india_q, limit=_WANT_EACH)
            + _fetch_rss(_MACRO_QUERY, limit=_WANT_EACH))
    global_heads, india_heads = _route(pool, sector_key)
    headlines = global_heads + india_heads          # for the Sources strip

    structural = TILT.profile(sector_key).get("text", "")
    read = (_synthesize(config, sector_name, structural, global_heads, india_heads)
            if headlines or config else None)

    if read is None and not headlines:
        # nothing fresh and no synthesis -> reuse the last good entry if any
        prev = _latest_for(cache, sector_key)
        if prev:
            prev = dict(prev)
            prev["stale"] = True
            return prev
    if read is None:
        read = _deterministic(sector_name, structural, global_heads, india_heads,
                              fundamentals)

    entry = {
        "sector_key": sector_key,
        "sector_name": sector_name,
        "news_snapshot_date": today,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "updated_display": datetime.now(timezone.utc).strftime("%d %b %Y"),
        "label": read["label"],
        "lines": read.get("lines", []),
        "global_items": read.get("global_items", []),
        "india_items": read.get("india_items", []),
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
