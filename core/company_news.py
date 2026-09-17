"""
company_news.py
---------------
"Company News & Outlook" — a company-specific intelligence layer, separate from
the sector-level current-cycle read in ``core.market_context`` (which is left
untouched). Given the analysed company it retrieves, validates, classifies,
deduplicates and ranks recent trusted news about THAT company, and renders it in
the FundaCheck card system.

Pipeline (every step is real-data only — no fabricated article, date or source):

  identity → aliases/symbol → targeted queries → trusted-source RSS →
  source validation → company-relevance scoring → event + time-horizon
  classification → paraphrase-aware dedup → composite ranking → top 10-12 →
  optional grounded LLM summary (deterministic fallback) → UI.

The LLM may only SUMMARISE/label a real retrieved article; it never invents an
event. If it is unavailable the summary falls back to the headline + RSS blurb.
"""

from __future__ import annotations

import csv
import json
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import escape
from pathlib import Path
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import requests

# Reuse two STABLE, read-only helpers from the sector news module. We do NOT call
# or change its retrieval/synthesis — only borrow the HTML cleaner and the
# trusted-source vocabulary so the two layers agree on what "trusted" means.
from .market_context import _clean as _strip_html
from .market_context import _TRUSTED as _SECTOR_TRUSTED

_ROOT = Path(__file__).resolve().parent.parent
_MASTER = _ROOT / "data" / "company_master.csv"
CACHE_PATH = _ROOT / "data" / "company_news_cache.json"

_GNEWS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
_TIMEOUT = 12
_MAX_AGE_DAYS = 365            # strategic items may be up to ~12 months old
_TARGET = 12                   # 10-12 articles when enough exist
_INITIAL = 3                   # cards shown before "See all …"

# ---------------------------------------------------------------------------
# trusted sources + quality tiers (primary filings rank above wires above sites)
# ---------------------------------------------------------------------------
_PRIMARY = ("bseindia", "nseindia", "sebi.gov", "sebi.org", "rbi.org",
            "annualreport", "investor", "/filings", "sec.gov")
_WIRES = ("reuters", "bloomberg")
_MAJOR = ("business-standard", "businessstandard", "economictimes", "livemint",
          "/mint", "moneycontrol", "financialexpress", "thehindubusinessline",
          "businessline", "cnbctv18", "ndtvprofit", "zerodha")
# Anything trusted at all — union of the sector vocabulary and the above.
_TRUSTED = tuple(sorted(set(_SECTOR_TRUSTED) | set(_PRIMARY) | set(_WIRES) | set(_MAJOR)))

_CORP_SUFFIX = re.compile(
    r"\b(limited|ltd|ltd\.|inc|incorporated|corporation|corp|company|co|plc|"
    r"pvt|private|holdings|industries|enterprises|india|the)\b\.?", re.I)

# ---------------------------------------------------------------------------
# event categories + time horizons
# ---------------------------------------------------------------------------
CATEGORIES = ("Earnings", "Management", "Strategy", "Expansion / Capex",
              "M&A / Partnership", "Regulation", "Operations",
              "Demand / Pricing", "Industry / Macro", "Risk", "Other")

# keyword → category (checked in priority order; first hit wins)
_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("M&A / Partnership", ("acquire", "acquisition", "merger", "merges", "stake",
                           "buyout", "takeover", "joint venture", " jv ", "partnership",
                           "tie-up", "tie up", "demerger", "demerge", "spin off", "spin-off")),
    ("Expansion / Capex", ("capex", "capacity", "expansion", "expand", "new plant",
                           "facility", "factory", "greenfield", "brownfield",
                           "invest ", "investment of", "crore capex", "sets up", "to build")),
    ("Earnings", ("q1", "q2", "q3", "q4", "quarter", "quarterly", "results", "profit",
                  "net profit", "revenue", "earnings", "ebitda", "margin beat", "pat ")),
    ("Management", ("ceo", "cfo", "managing director", " md ", "chairman", "appoint",
                    "resign", "steps down", "board", "management", "leadership",
                    "commentary", "guidance", "says ", "outlook")),
    ("Regulation", ("sebi", "rbi", "cci", "regulat", "tax", "gst", "tariff", "policy",
                    "approval", "approves", "probe", "penalty", "ban", "ruling", "court",
                    "tribunal", "compliance", "notice")),
    ("Strategy", ("strategy", "strategic", "roadmap", "vision", "plan", "restructur",
                  "pivot", "roll out", "foray", "enters", "launch")),
    ("Operations", ("production", "output", "operations", "plant", "shutdown",
                    "disruption", "supply", "volume", "utilisation", "utilization")),
    ("Demand / Pricing", ("demand", "price", "pricing", "hike", "cuts price", "order book",
                          "orders", "contract", "deal worth", "bags order", "wins order")),
    ("Risk", ("fraud", "default", "downgrade", "lawsuit", "loss widens", "warning",
              "warns", "insolvency", "debt", "fire", "recall", "cyber")),
    ("Industry / Macro", ("sector", "industry", "crude", "rupee", "interest rate",
                          "inflation", "global", "commodity")),
]

_MATERIALITY = {
    "M&A / Partnership": 0.95, "Expansion / Capex": 0.90, "Risk": 0.90,
    "Strategy": 0.85, "Regulation": 0.75, "Management": 0.70, "Earnings": 0.72,
    "Operations": 0.60, "Demand / Pricing": 0.62, "Industry / Macro": 0.40,
    "Other": 0.30,
}
# categories whose relevance is naturally short vs long dated
_LONG_CATS = {"Strategy", "Expansion / Capex", "M&A / Partnership"}
_SHORT_CATS = {"Earnings", "Demand / Pricing", "Operations"}
_ONGOING_CATS = {"Regulation", "Industry / Macro", "Risk"}


# ---------------------------------------------------------------------------
# data model
# ---------------------------------------------------------------------------
@dataclass
class CompanyNewsItem:
    title: str
    summary: str = ""
    url: str = ""
    source: str = ""
    published_at: str | None = None       # YYYY-MM-DD
    category: str = "Other"
    horizon: str = "ONGOING"              # SHORT TERM / MEDIUM TERM / LONG TERM / ONGOING
    relevance_score: float = 0.0
    source_quality: float = 0.0
    event_key: str = ""
    geography: str = "India"
    company: str = ""
    retrieved_at: str = ""
    _ts: float = 0.0

    def as_public(self) -> dict:
        d = asdict(self)
        d.pop("_ts", None)
        return d


@dataclass
class CompanyIdentity:
    name: str                              # canonical display name (no corp suffix)
    raw_name: str                          # exactly as uploaded
    symbol: str = ""                       # NSE symbol
    aliases: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# identity resolution (reuses data/company_master.csv — the same master the rest
# of the app uses; never a second incompatible identity system)
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_MASTER_CACHE: list[dict] | None = None


def _master_rows() -> list[dict]:
    global _MASTER_CACHE
    if _MASTER_CACHE is None:
        try:
            _MASTER_CACHE = list(csv.DictReader(_MASTER.open(newline="", encoding="utf-8")))
        except OSError:
            _MASTER_CACHE = []
    return _MASTER_CACHE


def _canonical(name: str) -> str:
    """Drop corporate suffixes/filler and tidy spacing: 'ITC Limited' -> 'ITC'."""
    base = _CORP_SUFFIX.sub(" ", name or "")
    base = re.sub(r"[.,]", " ", base)
    base = re.sub(r"\s+", " ", base).strip(" -&")
    return base or (name or "").strip()


def resolve_identity(company_name: str, nse_symbol: str | None = None) -> CompanyIdentity:
    """Resolve an uploaded name (+ optional symbol) to a stable identity with the
    NSE symbol and a set of aliases. Falls back gracefully to the given name."""
    raw = (company_name or "").strip()
    symbol = (nse_symbol or "").strip().upper()
    master_name = ""
    if not symbol and raw:                        # look the symbol up in the master
        key = _norm(raw)
        for r in _master_rows():
            if _norm(r.get("name", "")) == key:
                symbol, master_name = str(r.get("nse_symbol", "")).upper(), r.get("name", "")
                break
        else:
            for r in _master_rows():
                n = _norm(r.get("name", ""))
                if n and (n in key or key in n) and abs(len(n) - len(key)) <= 6:
                    symbol, master_name = str(r.get("nse_symbol", "")).upper(), r.get("name", "")
                    break
    if symbol and not master_name:                # symbol given → get the official name
        for r in _master_rows():
            if str(r.get("nse_symbol", "")).upper() == symbol:
                master_name = r.get("name", "")
                break

    display_src = master_name or raw
    canon = _canonical(display_src)
    aliases = {display_src, canon, _canonical(raw), raw}
    if symbol:
        aliases.add(symbol)
    aliases = tuple(sorted({a.strip() for a in aliases if a and a.strip()},
                           key=lambda a: -len(a)))
    return CompanyIdentity(name=canon or raw, raw_name=raw, symbol=symbol, aliases=aliases)


# ---------------------------------------------------------------------------
# targeted query generation
# ---------------------------------------------------------------------------
def generate_queries(identity: CompanyIdentity) -> list[tuple[str, str]]:
    """A set of (query, when-window) pairs targeting company-specific dimensions.
    Quoted company name keeps results on-company; the window differs by intent —
    recent for operational news, up to a year for strategic developments."""
    name = identity.name
    q = f'"{name}"'
    recent, strat = "45d", "365d"
    pairs = [
        (f'{q} (results OR earnings OR profit OR revenue OR quarterly)', recent),
        (f'{q} (management OR guidance OR commentary OR outlook)', recent),
        (f'{q} (demand OR pricing OR margins OR "order book" OR contract)', recent),
        (f'{q} (capex OR expansion OR capacity OR investment OR facility)', strat),
        (f'{q} (acquisition OR merger OR stake OR partnership OR "joint venture")', strat),
        (f'{q} (strategy OR restructuring OR demerger OR "new business")', strat),
        (f'{q} (SEBI OR regulation OR regulatory OR tariff OR policy)', strat),
        (f'{q} latest news', recent),
    ]
    # a symbol-scoped recent query catches exchange-disclosure phrasing
    if identity.symbol and identity.symbol != name.upper():
        pairs.append((f'"{identity.symbol}" (NSE OR BSE OR shares OR stock)', recent))
    return pairs


# ---------------------------------------------------------------------------
# fetch (dedicated to this module so market_context stays untouched; also keeps
# the RSS <description> that the deterministic summary needs)
# ---------------------------------------------------------------------------
def _fetch(query: str, when: str, limit: int = 12) -> list[dict]:
    url = _GNEWS.format(q=quote_plus(f"{query} when:{when}"))
    try:
        resp = requests.get(url, timeout=_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0 (FundaCheck company news)"})
        if resp.status_code != 200 or not resp.text:
            return []
        root = ET.fromstring(resp.text)
    except (requests.RequestException, ET.ParseError):
        return []
    out = []
    for it in root.iter("item"):
        title = _strip_html(it.findtext("title") or "")
        src = _strip_html(it.findtext("{http://news.google.com}source")
                          or it.findtext("source") or "")
        if not src and " - " in title:
            title, src = title.rsplit(" - ", 1)
        desc = _strip_html(it.findtext("description") or "")
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        try:
            dt = parsedate_to_datetime(pub) if pub else None
        except (TypeError, ValueError):
            dt = None
        out.append({"title": title.strip(), "source": src.strip(), "url": link,
                    "description": desc, "date": dt.date().isoformat() if dt else None,
                    "_ts": dt.timestamp() if dt else 0.0})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# source validation + quality
# ---------------------------------------------------------------------------
def _nrm(s: str) -> str:
    """Alphanumeric-only lowercase, so 'Business Standard', 'business-standard'
    and '…business-standard.com/…' all match the same trusted token."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_PRIMARY_N = tuple(_nrm(t) for t in _PRIMARY)
_WIRES_N = tuple(_nrm(t) for t in _WIRES)
_MAJOR_N = tuple(_nrm(t) for t in _MAJOR)
_TRUSTED_N = tuple(_nrm(t) for t in _TRUSTED)


def _blob(item: dict) -> str:
    return _nrm(str(item.get("url", "")) + " " + str(item.get("source", "")))


def is_trusted(item: dict) -> bool:
    b = _blob(item)
    return any(t and t in b for t in _TRUSTED_N)


def source_quality(item: dict) -> float:
    b = _blob(item)
    if any(t and t in b for t in _PRIMARY_N):
        return 1.0
    if any(t and t in b for t in _WIRES_N):
        return 0.9
    if any(t and t in b for t in _MAJOR_N):
        return 0.8
    if is_trusted(item):
        return 0.6
    return 0.0                              # untrusted → excluded upstream


# ---------------------------------------------------------------------------
# company-relevance scoring
# ---------------------------------------------------------------------------
_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "with",
         "as", "at", "by", "is", "its", "it", "from", "over", "after", "amid"}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _STOP and len(w) > 1}


def company_relevance(item: dict, identity: CompanyIdentity) -> float:
    """0..1. High when the company is the primary subject of the headline."""
    title = (item.get("title") or "")
    tl = title.lower()
    desc = (item.get("description") or "").lower()
    canon = identity.name.lower()
    sym = identity.symbol.lower()
    score = 0.0
    # full canonical name present in the title = strong
    if canon and canon in tl:
        score = 0.7
        if tl.strip().startswith(canon):            # company leads the headline
            score = 0.9
    elif sym and re.search(rf"\b{re.escape(sym)}\b", tl):
        score = 0.65
    else:
        # distinctive alias token(s) in the title
        core = _tokens(canon)
        if core and core.issubset(_tokens(title)):
            score = 0.6
        elif canon and canon in desc:
            score = 0.35                            # only body-mention → medium/low
        else:
            return 0.0
    # a second mention (title + body) firms it up
    if canon and canon in desc and score < 0.9:
        score = min(0.95, score + 0.08)
    return round(score, 3)


# ---------------------------------------------------------------------------
# event + horizon classification
# ---------------------------------------------------------------------------
_STRONG_EVENT = ("acquire", "acquisition", "merger", "stake", "demerger", "capex",
                 "expansion", "capacity", "invest", "result", "profit", "revenue",
                 "earnings", "order", "contract", "launch", "appoint", "resign",
                 "sebi", "regulat", "partnership", "joint venture", "fundraise",
                 "bond", "deal", "guidance", "restructur")
_PRICE_MOVE = re.compile(
    r"\b(shares?|stock|share price)\b.*\b(slip|slips|rise|rises|gain|gains|fall|falls|"
    r"drop|drops|edge|edges|higher|lower|decline|declines|jump|jumps|slide|slides|"
    r"%|per ?cent|52-week|top (gainer|loser|gainers|losers)|buy the dip|target price)",
    re.I)


def _is_price_move(title: str) -> bool:
    """A pure market-reaction headline (share price up/down) carrying no real
    corporate event — should not masquerade as a material M&A/Strategy story."""
    t = (title or "").lower()
    if not _PRICE_MOVE.search(t):
        return False
    return not any(k in t for k in _STRONG_EVENT)


def classify_category(item: dict) -> str:
    title = item.get("title") or ""
    if _is_price_move(title):                 # market noise → low-materiality bucket
        return "Other"
    hay = (title + " " + (item.get("description") or "")).lower()
    for cat, kws in _CATEGORY_RULES:
        if any(k in hay for k in kws):
            return cat
    return "Other"


def _age_days(item: dict) -> float:
    ts = item.get("_ts") or 0.0
    return (time.time() - ts) / 86400.0 if ts else 9999.0


def classify_horizon(item: dict, category: str | None = None) -> str:
    cat = category or classify_category(item)
    hay = ((item.get("title") or "") + " " + (item.get("description") or "")).lower()
    if any(w in hay for w in ("next year", "by 2027", "by 2028", "over the year",
                              "long term", "long-term", "five-year", "5-year", "roadmap")):
        return "LONG TERM"
    if cat in _LONG_CATS:
        return "LONG TERM"
    if any(w in hay for w in ("upcoming", "to report", "next quarter", "q1", "q2",
                              "q3", "q4", "this week", "monsoon", "festive")):
        return "SHORT TERM"
    if cat in _SHORT_CATS:
        return "SHORT TERM"
    if cat in _ONGOING_CATS:
        return "ONGOING"
    return "MEDIUM TERM"


# ---------------------------------------------------------------------------
# deduplication — paraphrase-aware (rapidfuzz token-set, Jaccard fallback)
# ---------------------------------------------------------------------------
def _similar(a: str, b: str) -> float:
    try:
        from rapidfuzz import fuzz
        return fuzz.token_set_ratio(a, b) / 100.0
    except Exception:                               # pragma: no cover - fallback
        ta, tb = _tokens(a), _tokens(b)
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)


# generic words that carry no event identity (so paraphrases still match)
_GENERIC = {"q1", "q2", "q3", "q4", "net", "yoy", "qoq", "pct", "percent", "rises",
            "rise", "jumps", "jump", "up", "down", "higher", "lower", "gains", "slips",
            "shares", "share", "stock", "rs", "crore", "cr", "report", "reports",
            "reported", "results", "result", "posts", "post", "sees", "may", "plans",
            "plan", "eyes", "aims", "aim", "major", "big", "new", "firm", "company",
            "outlines", "aggressive", "growth", "quarter", "first", "second", "third",
            "fourth", "year", "yr"}


def _salient(item: dict, identity: CompanyIdentity | None) -> set[str]:
    """Content tokens of a headline minus company aliases and generic words, with
    trivial singularisation so 'hotel'/'hotels' match."""
    title = (item.get("title") or "").lower()
    if identity:
        for a in identity.aliases:
            title = title.replace(a.lower(), " ")
    out = set()
    for w in _tokens(title):
        if w in _GENERIC:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") else w)
    return out


def event_key(item: dict, identity: CompanyIdentity | None = None) -> str:
    """Signature of the underlying event: salient tokens, company name stripped."""
    return " ".join(sorted(_salient(item, identity))[:8])


def dedup(items: list[dict], identity: CompanyIdentity | None = None,
          threshold: float = 0.82) -> list[dict]:
    """Collapse items describing the same event; keep the strongest version
    (higher source quality, then relevance, then recency). Catches paraphrases via
    salient-token overlap, and treats same-period earnings items as one event."""
    def strength(it):
        return (it.get("source_quality", source_quality(it)),
                it.get("relevance_score", 0.0), it.get("_ts", 0.0))

    kept: list[dict] = []
    kept_salient: list[set[str]] = []
    for it in sorted(items, key=strength, reverse=True):
        it["event_key"] = it.get("event_key") or event_key(it, identity)
        sal = _salient(it, identity)
        dup = False
        for k, ks in zip(kept, kept_salient):
            same_cat = it.get("category") == k.get("category")
            if _similar(it.get("title", ""), k.get("title", "")) >= threshold:
                dup = True
                break
            if not same_cat:
                continue
            # a company reports earnings once per period — same-category earnings
            # items within ~3 weeks are the same event
            if it.get("category") == "Earnings" and \
                    abs((it.get("_ts") or 0) - (k.get("_ts") or 0)) <= 21 * 86400:
                dup = True
                break
            if len(sal & ks) >= 2:                     # shared salient content
                dup = True
                break
            if sal and ks and len(sal & ks) / len(sal | ks) >= 0.5:
                dup = True
                break
        if not dup:
            kept.append(it)
            kept_salient.append(sal)
    return kept


# ---------------------------------------------------------------------------
# ranking + diverse selection
# ---------------------------------------------------------------------------
def _recency(item: dict) -> float:
    age = _age_days(item)
    if age <= 7:
        return 1.0
    if age >= 365:
        return 0.3
    return round(1.0 - 0.7 * (age - 7) / (365 - 7), 3)


def _composite(item: dict) -> float:
    return round(0.35 * item.get("relevance_score", 0.0)
                 + 0.20 * _MATERIALITY.get(item.get("category", "Other"), 0.3)
                 + 0.20 * item.get("source_quality", 0.0)
                 + 0.15 * _recency(item)
                 + 0.10 * (1.0 if item.get("source_quality", 0) >= 1.0 else 0.0), 4)


def rank_and_select(items: list[dict], limit: int = _TARGET,
                    per_category: int = 3) -> list[dict]:
    """Rank by composite score, but spread event categories so the list (and its
    first three) is diverse rather than 10 of one type. At each step take the
    highest-scoring remaining item whose category is least represented so far,
    which interleaves categories while still honouring the ranking within a tier."""
    remaining = sorted(items, key=_composite, reverse=True)   # rank order
    picked: list[dict] = []
    cat_count: dict[str, int] = {}
    while remaining and len(picked) < limit:
        elig = [it for it in remaining
                if cat_count.get(it.get("category", "Other"), 0) < per_category]
        if not elig:                                          # caps exhausted → fill by rank
            elig = remaining
        min_seen = min(cat_count.get(it.get("category", "Other"), 0) for it in elig)
        choice = next(it for it in elig
                      if cat_count.get(it.get("category", "Other"), 0) == min_seen)
        picked.append(choice)
        remaining.remove(choice)
        c = choice.get("category", "Other")
        cat_count[c] = cat_count.get(c, 0) + 1
    return picked[:limit]


# ---------------------------------------------------------------------------
# grounded summaries (LLM optional; deterministic fallback — never fabricated)
# ---------------------------------------------------------------------------
def _deterministic_summary(item: dict, identity: CompanyIdentity) -> str:
    desc = (item.get("description") or "").strip()
    if desc:
        # first 2 sentences of the real RSS blurb
        parts = re.split(r"(?<=[.!?])\s+", desc)
        s = " ".join(parts[:2]).strip()
        if len(s) > 40:
            return s
    src = item.get("source") or "the source"
    return (f"{item.get('title', '').strip()}. Reported by {src}"
            f"{(' on ' + item['date']) if item.get('date') else ''}; "
            f"relevant to {identity.name} as a {item.get('category', 'company').lower()} development.")


def _llm_summaries(items: list[dict], identity: CompanyIdentity, config) -> dict[int, str]:
    """Ask the configured LLM for a grounded 2-3 sentence read per item, in ONE
    call. Returns {index: summary}. Empty on any failure (caller falls back)."""
    live = config is not None and (getattr(config, "is_live", False)
                                   or any(getattr(c, "is_live", False)
                                          for c in getattr(config, "fallbacks", [])))
    if not live or not items:
        return {}
    try:
        from .llm import post
    except Exception:
        return {}
    lines = "\n".join(
        f"[{i}] ({it.get('category')}) {it.get('title')} :: {it.get('description') or ''}"
        for i, it in enumerate(items))
    system = (
        "You summarise real news for one Indian company for a non-expert investor. "
        "Use ONLY the headline and blurb given — never invent events, numbers, "
        "dates, quotes or management intentions. 2-3 short sentences each: what "
        "happened, why it could matter to THIS company, and the rough time horizon. "
        "Use tentative language ('could', 'may', 'relevant to') — never claim a "
        "stock will move. Separate fact from interpretation. No investment advice.")
    user = (f"Company: {identity.name} ({identity.symbol or 'NSE'}).\n"
            f"Articles:\n{lines}\n\n"
            'Return STRICT JSON: {"summaries": [{"i": <index>, "text": "..."}, ...]}. '
            "No text outside the JSON.")
    try:
        raw = post(config, [{"role": "system", "content": system},
                            {"role": "user", "content": user}], json_mode=True)
        data = json.loads(raw)
    except Exception:
        return {}
    out: dict[int, str] = {}
    for row in (data.get("summaries") or []):
        try:
            i = int(row.get("i"))
            t = str(row.get("text") or "").strip()
        except (TypeError, ValueError):
            continue
        if t:
            out[i] = t
    return out


# ---------------------------------------------------------------------------
# build the ranked item list from raw candidates (pure — unit-testable)
# ---------------------------------------------------------------------------
def build_items(raw: list[dict], identity: CompanyIdentity, config=None,
                limit: int = _TARGET) -> list[CompanyNewsItem]:
    now = datetime.now(timezone.utc).isoformat()
    cutoff = time.time() - _MAX_AGE_DAYS * 86400
    cands: list[dict] = []
    seen_urls: set[str] = set()
    for it in raw:
        if not it.get("title"):
            continue
        if not is_trusted(it):                      # unverifiable source → drop
            continue
        if (it.get("_ts") or 0.0) < cutoff:         # too old
            continue
        u = it.get("url") or ""
        if u and u in seen_urls:
            continue
        seen_urls.add(u)
        rel = company_relevance(it, identity)
        if rel < 0.35:                              # not materially about the company
            continue
        cat = classify_category(it)
        it = dict(it)
        it["relevance_score"] = rel
        it["source_quality"] = source_quality(it)
        it["category"] = cat
        it["horizon"] = classify_horizon(it, cat)
        it["event_key"] = event_key(it, identity)
        cands.append(it)

    unique = dedup(cands, identity)
    chosen = rank_and_select(unique, limit=limit)

    summaries = _llm_summaries(chosen, identity, config)
    items: list[CompanyNewsItem] = []
    for i, it in enumerate(chosen):
        summ = summaries.get(i) or _deterministic_summary(it, identity)
        items.append(CompanyNewsItem(
            title=it.get("title", ""), summary=summ, url=it.get("url", ""),
            source=it.get("source", ""), published_at=it.get("date"),
            category=it.get("category", "Other"), horizon=it.get("horizon", "ONGOING"),
            relevance_score=it.get("relevance_score", 0.0),
            source_quality=it.get("source_quality", 0.0),
            event_key=it.get("event_key", ""), company=identity.name,
            retrieved_at=now, _ts=it.get("_ts", 0.0)))
    return items


# ---------------------------------------------------------------------------
# caching (per company per day; never caches an empty/error result as success)
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
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------
def get_company_news(company_name: str, nse_symbol: str | None = None,
                     config=None, *, force: bool = False) -> dict:
    """Retrieve + process company news. Cached per company per UTC day; on a soft
    failure returns the last good cache (dated) then an honest empty state.
    Never raises — the dashboard must survive a news outage."""
    identity = resolve_identity(company_name, nse_symbol)
    today = datetime.now(timezone.utc).date().isoformat()
    key = f"{(identity.symbol or _norm(identity.name))}:{today}"
    cache = _load_cache()
    if not force and key in cache:
        return cache[key]

    raw: list[dict] = []
    try:
        for query, when in generate_queries(identity):
            raw.extend(_fetch(query, when))
    except Exception:                               # noqa: BLE001 — never crash the app
        raw = []

    items: list[CompanyNewsItem] = []
    if raw:
        try:
            items = build_items(raw, identity, config)
        except Exception:                           # noqa: BLE001
            items = []

    if not items:
        prev = cache.get(f"{(identity.symbol or _norm(identity.name))}:")  # unused; explicit
        # reuse the newest good entry for this company if today's fetch failed
        good = [v for k, v in cache.items()
                if k.startswith((identity.symbol or _norm(identity.name)) + ":")
                and v.get("items")]
        if good:
            best = max(good, key=lambda v: v.get("updated", ""))
            best = dict(best)
            best["stale"] = True
            return best
        entry = {"company": identity.name, "symbol": identity.symbol,
                 "items": [], "updated": today,
                 "updated_display": datetime.now(timezone.utc).strftime("%d %b %Y"),
                 "empty": True, "stale": False}
        return entry                                # not cached (empty is not success)

    entry = {"company": identity.name, "symbol": identity.symbol,
             "items": [it.as_public() for it in items], "updated": today,
             "updated_display": datetime.now(timezone.utc).strftime("%d %b %Y"),
             "empty": False, "stale": False}
    cache[key] = entry
    if len(cache) > 120:
        for k in sorted(cache, key=lambda k: cache[k].get("updated", ""))[:-120]:
            cache.pop(k, None)
    _save_cache(cache)
    return entry


# ---------------------------------------------------------------------------
# rendering — FundaCheck card system (URLs live ONLY in the Sources strip)
# ---------------------------------------------------------------------------
_HORIZON_CLS = {"SHORT TERM": "hz-s", "MEDIUM TERM": "hz-m",
                "LONG TERM": "hz-l", "ONGOING": "hz-o"}

_CSS = """
<style>
.cn{--ink:#15201A;--ink-2:#3F4744;--mute:#8B918E;--mute-2:#9AA09D;--line:#E6EBE7;
  --line-2:#F0F3F0;--brand:#177245;--pos:#2F9E63;--warn:#C68A2E;--warn-deep:#B5761F;
  --neg:#B4483C;--mono:ui-monospace,Menlo,Consolas,monospace;
  font-family:'Plus Jakarta Sans',system-ui,sans-serif;color:var(--ink);
  font-variant-numeric:tabular-nums}
.cn *{box-sizing:border-box}
.cn .cn-h{padding:6px 4px 2px}
.cn .cn-t{font-size:22px;font-weight:800;letter-spacing:-.6px}
.cn .cn-s{font-size:13px;color:var(--mute);padding-top:6px;max-width:78ch;line-height:1.55}
.cn .cn-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));
  gap:14px;padding-top:14px}
.cn .nc{background:#fff;border:1px solid var(--line);border-radius:16px;padding:18px 20px 16px;
  box-shadow:0 1px 2px rgba(21,32,26,.04),0 6px 18px rgba(21,32,26,.05);
  display:flex;flex-direction:column;position:relative}
.cn .nc-n{font-family:var(--mono);font-size:11px;font-weight:700;color:#C3CAC6;letter-spacing:.5px}
.cn .nc-tags{display:flex;flex-wrap:wrap;gap:7px;align-items:center;padding:6px 0 8px}
.cn .nc-cat{font-family:var(--mono);font-size:9.5px;font-weight:700;letter-spacing:.8px;
  text-transform:uppercase;color:var(--brand);background:var(--pos-tint,#EEF4F0);
  border:1px solid #CFE2D7;border-radius:6px;padding:3px 8px}
.cn .nc-hz{font-family:var(--mono);font-size:9.5px;font-weight:700;letter-spacing:.8px;
  text-transform:uppercase;border-radius:6px;padding:3px 8px;border:1px solid transparent}
.cn .hz-s{color:#B5761F;background:#FDF3E2;border-color:#F0D9AE}
.cn .hz-m{color:#177245;background:#EEF4F0;border-color:#CFE2D7}
.cn .hz-l{color:#0F3D27;background:#E7F2EC;border-color:#BFDDCC}
.cn .hz-o{color:#5F6663;background:#F0F3F0;border-color:#E1E6E1}
.cn .nc-ttl{font-size:15px;font-weight:800;line-height:1.32;letter-spacing:-.2px;color:var(--ink)}
.cn .nc-b{font-size:13px;line-height:1.6;color:var(--ink-2);padding-top:8px;flex:1}
.cn .nc-f{display:flex;align-items:center;gap:8px;padding-top:12px;margin-top:10px;
  border-top:1px solid var(--line-2);font-size:11.5px;color:var(--mute-2)}
.cn .nc-src{font-weight:700;color:var(--ink-2)}
.cn a.nc-src{color:var(--brand);text-decoration:none;font-weight:700}
.cn a.nc-src:hover{text-decoration:underline}
.cn a.nc-src .nc-date{color:var(--mute-2);font-weight:600}
.cn .cn-upd{font-family:var(--mono);font-size:9.5px;font-weight:700;letter-spacing:.8px;
  text-transform:uppercase;color:var(--mute-2);padding:14px 4px 2px}
.cn .cn-empty{background:#FBFCFB;border:1px dashed #D7DED8;border-radius:14px;
  padding:22px 20px;color:var(--mute);font-size:13.5px;line-height:1.6;margin-top:12px}
.cn .cn-hidden{display:none}
.cn .cn-toggle{margin:16px 4px 2px;background:#fff;border:1px solid #DFE6E1;border-radius:12px;
  font-family:inherit;font-size:13px;font-weight:700;color:var(--brand);padding:10px 18px;
  cursor:pointer;transition:border-color .15s,background .15s}
.cn .cn-toggle:hover{border-color:var(--brand);background:#EEF4F0}
.cn.cn-sec{margin-top:22px;border-top:1px solid var(--line);padding-top:20px}
</style>
"""


def _card(i: int, it: dict, hidden: bool = False) -> str:
    hz = it.get("horizon", "ONGOING")
    hzcls = _HORIZON_CLS.get(hz, "hz-o")
    date = it.get("published_at") or ""
    src = escape(it.get("source") or "source")
    url = escape(it.get("url") or "")
    cls = "nc cn-hidden" if hidden else "nc"
    date_html = f'<span class="nc-date">&middot; {escape(date)}</span>' if date else ""
    # source + date is the clickable article link (opens in a new tab); the URL
    # lives on the card itself now, not in a separate Sources section.
    if url:
        foot = (f'<a class="nc-src" href="{url}" target="_blank" rel="noopener noreferrer">'
                f'{src} ↗</a>{date_html}')
    else:
        foot = f'<span class="nc-src">{src}</span>{date_html}'
    return (
        f'<div class="{cls}"><span class="nc-n">{i:02d}</span>'
        f'<div class="nc-tags"><span class="nc-cat">{escape(it.get("category","Other"))}</span>'
        f'<span class="nc-hz {hzcls}">{escape(hz)}</span></div>'
        f'<div class="nc-ttl">{escape(it.get("title",""))}</div>'
        f'<div class="nc-b">{escape(it.get("summary",""))}</div>'
        f'<div class="nc-f">{foot}</div></div>')


_TOGGLE_JS = """
<script>
(function(){
  window.__cnToggle = function(){
    var sec = document.querySelector('.cn-sec'); if(!sec) return;
    var open = sec.getAttribute('data-open') === '1';
    sec.querySelectorAll('.nc').forEach(function(c, i){
      if(i >= 3) c.classList.toggle('cn-hidden', open); });
    sec.setAttribute('data-open', open ? '0' : '1');
    var b = document.getElementById('cnToggle');
    if(b) b.innerHTML = open ? ('See all ' + b.getAttribute('data-total') + ' developments \\u2192')
                             : 'Show fewer \\u2191';
  };
})();
</script>
"""


def render_section(entry: dict) -> str:
    """In-shell section HTML (styles + header + all cards with the extras hidden +
    a JS 'See all' toggle + a Sources strip). Rendered INSIDE the Sector Lens
    iframe, before its footer — so it flows with that component (no overlap) and
    the expand toggle needs no network (all cards are already present, just hidden).
    Cards carry NO links; every URL is in the Sources strip (FundaCheck convention)."""
    company = escape(entry.get("company") or "this company")
    head = ('<div class="cn-h"><div class="cn-t">Company News &amp; Outlook</div>'
            '<div class="cn-s">Material developments, management actions and external '
            f'factors that could matter to {company} over the next quarter and year. '
            'Retrieved from trusted sources; not investment advice.</div></div>')

    items = list(entry.get("items") or [])
    if entry.get("empty") or not items:
        inner = (head + '<div class="cn-empty">No recent company-specific developments '
                 'were found from the available trusted sources.</div>')
        return f'<section class="cn cn-sec">{_CSS}{inner}</section>'

    total = len(items)
    cards = "".join(_card(i, it, hidden=(i > _INITIAL)) for i, it in enumerate(items, 1))
    toggle = ""
    if total > _INITIAL:
        toggle = (f'<button type="button" class="cn-toggle" id="cnToggle" '
                  f'data-total="{total}" onclick="__cnToggle()">'
                  f'See all {total} developments &rarr;</button>')

    # Each card links to its own article; there is no separate Sources section.
    upd = escape(str(entry.get("updated_display") or ""))
    updated = (f'<div class="cn-upd">Updated {upd}'
               f'{" &middot; cached" if entry.get("stale") else ""} &middot; '
               'trusted sources &middot; not investment advice</div>') if upd else ""

    inner = head + f'<div class="cn-grid" id="cnGrid">{cards}</div>' + toggle + updated
    return f'<section class="cn cn-sec" data-open="0">{_CSS}{inner}{_TOGGLE_JS}</section>'
