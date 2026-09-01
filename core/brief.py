"""
core/brief.py
-------------
The "Company Brief" that sits above the Ask-AI chat.

Source-first and grounded. The pipeline is:

  1. resolve the company name against a *static* master shipped in the repo
     (data/company_master.csv → NSE symbol + ISIN); no live NSE lookup.
  2. discover the company's latest official documents. Screener.in is used only
     as a link *index* — the URL we keep and cite is always the official
     BSE/company document it points to, never Screener itself.
  3. download those PDFs once (cached on disk by URL hash) and pull their text
     with pypdf. No OCR in v1: a scanned/unreadable PDF is reported as such, with
     its source link, rather than summarised from guesswork.
  4. hand ONLY that retrieved text to the existing LLM layer (core/llm) and ask
     for three sections. If a fact is not in the text the model must say
     "Not stated in the available filings." — it never uses general knowledge.

Everything degrades gracefully: any step that fails leaves the chat working and
shows whatever source links were found.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .llm import LLMConfig, post

APP_DIR = Path(__file__).resolve().parent.parent
MASTER = APP_DIR / "data" / "company_master.csv"
# Runtime-only cache. On Streamlit Community Cloud the container filesystem is
# ephemeral and wiped on restart/redeploy — that is fine: a cold start simply
# re-downloads. The point of the cache is to avoid re-downloading within a
# running container and across reruns, not durable storage.
CACHE_DIR = APP_DIR / ".cache" / "brief"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 20
# Token budget for the LLM context: transcript + presentation first (richest
# management commentary), annual report only if room. Kept small on purpose —
# Groq's free tier caps at ~8000 tokens/minute, so the whole request (context +
# prompt + reply) must stay well under that.
MAX_DOC_CHARS = 4000
MAX_CONTEXT_CHARS = 8000


# --------------------------------------------------------------------------
# data types
# --------------------------------------------------------------------------
@dataclass
class Doc:
    kind: str            # "Earnings call transcript" | "Investor presentation" | "Annual report"
    date: str            # human label, e.g. "Jul 2026" or "2026"
    url: str             # the OFFICIAL document URL (BSE / company), not Screener
    source: str          # "BSE filing" | "Company IR"
    text: str = ""       # extracted PDF text (filled by _load_text)
    readable: bool = True
    note: str = ""       # e.g. "scanned PDF — could not be machine-read"


@dataclass
class Brief:
    company: str
    identity: dict = field(default_factory=dict)        # {name, nse_symbol, isin}
    core_focus: str = ""
    key_initiatives: str = ""
    why_care: str = ""
    docs: list[Doc] = field(default_factory=list)       # sources actually used
    unavailable: str = ""                               # non-empty => nothing to show
    error: str = ""                                     # soft error note
    offline: bool = False                               # LLM not connected


# --------------------------------------------------------------------------
# 1. identity — static master, no live NSE call
# --------------------------------------------------------------------------
def _norm(s: str) -> str:
    s = s.lower()
    for junk in (" limited", " ltd", " ltd.", " (india)", " india", " corporation",
                 " corp", " company", " co.", " &", " and "):
        s = s.replace(junk, " ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _load_master() -> list[dict]:
    if not MASTER.exists():
        return []
    out = []
    with MASTER.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append(row)
    return out


def resolve_identity(company: str) -> dict | None:
    """company name -> {name, nse_symbol, isin} using the static master."""
    rows = _load_master()
    if not rows or not company:
        return None
    try:
        from rapidfuzz import fuzz, process
    except Exception:                                   # noqa: BLE001
        fuzz = process = None
    target = _norm(company)
    if process is not None:
        choice = process.extractOne(
            target, {i: _norm(r["name"]) for i, r in enumerate(rows)}.items(),
            scorer=fuzz.WRatio, processor=lambda x: x[1] if isinstance(x, tuple) else x)
        # process.extractOne over dict.items() returns ((idx, normname), score, key)
        if choice and choice[1] >= 80:
            r = rows[choice[0][0]]
            return {"name": r["name"], "nse_symbol": r["nse_symbol"], "isin": r["isin"]}
    # fallback: exact / substring match on normalised names
    for r in rows:
        if _norm(r["name"]) == target:
            return {"name": r["name"], "nse_symbol": r["nse_symbol"], "isin": r["isin"]}
    for r in rows:
        n = _norm(r["name"])
        if target and (target in n or n in target):
            return {"name": r["name"], "nse_symbol": r["nse_symbol"], "isin": r["isin"]}
    return None


# --------------------------------------------------------------------------
# 2. discovery — Screener page as an index of official document URLs
# --------------------------------------------------------------------------
def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def _is_official(url: str) -> bool:
    u = url.lower()
    return "bseindia.com" in u or "nseindia.com" in u


def discover_documents(symbol: str, sess: requests.Session | None = None) -> list[Doc]:
    """Read the public Screener page and return the latest official transcript,
    investor presentation and annual report (URLs point to BSE, not Screener)."""
    sess = sess or _session()
    from bs4 import BeautifulSoup
    docs: list[Doc] = []
    try:
        r = sess.get(f"https://www.screener.in/company/{symbol}/consolidated/", timeout=TIMEOUT)
        if r.status_code != 200 or "documents" not in r.text.lower():
            r = sess.get(f"https://www.screener.in/company/{symbol}/", timeout=TIMEOUT)
        if r.status_code != 200:
            return docs
    except requests.RequestException:
        return docs

    soup = BeautifulSoup(r.text, "html.parser")
    section = soup.find("section", id="documents") or soup

    # --- annual reports: <a>Annual Report YYYY</a> -> official BSE pdf ---
    best_ar = None
    for a in section.find_all("a"):
        txt = a.get_text(" ", strip=True)
        href = a.get("href", "")
        m = re.match(r"(?:from )?annual report\s*(\d{4})", txt.lower())
        if m and _is_official(href):
            yr = int(m.group(1))
            if best_ar is None or yr > best_ar[0]:
                best_ar = (yr, href)
    if best_ar:
        docs.append(Doc("Annual report", str(best_ar[0]), best_ar[1], "BSE filing"))

    # --- concalls: dated <li> rows with Transcript / PPT official links ---
    concall_block = section.find("div", class_=lambda c: c and "concalls" in c)
    transcript = ppt = None
    if concall_block:
        # Only the most-recent concalls: if the latest transcript/PPT isn't
        # publicly linked, omit it rather than reaching back to a stale (e.g.
        # 2023) filing while the presentation is current.
        for i, li in enumerate(concall_block.find_all("li")):
            if i >= 4:
                break
            date_el = li.find("div")
            date = date_el.get_text(" ", strip=True) if date_el else ""
            for a in li.find_all("a"):
                label = a.get_text(" ", strip=True).lower()
                href = a.get("href", "")
                if not _is_official(href):
                    continue
                if transcript is None and "transcript" in label:
                    transcript = Doc("Earnings call transcript", date, href, "BSE filing")
                elif ppt is None and ("ppt" in label or "present" in label):
                    ppt = Doc("Investor presentation", date, href, "BSE filing")
            if transcript and ppt:
                break
    if transcript:
        docs.append(transcript)
    if ppt:
        docs.append(ppt)
    return docs


# --------------------------------------------------------------------------
# 3. download + extract (cache on disk by URL hash; never cache a failure)
# --------------------------------------------------------------------------
def _cache_path(url: str) -> Path:
    h = hashlib.md5(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{h}.pdf"


def download_pdf(url: str, sess: requests.Session | None = None) -> Path | None:
    sess = sess or _session()
    path = _cache_path(url)
    if path.exists() and path.stat().st_size > 1024:
        return path                                     # already have it
    try:
        r = sess.get(url, timeout=TIMEOUT, headers={"Referer": "https://www.bseindia.com/"})
        ctype = r.headers.get("content-type", "").lower()
        if r.status_code != 200 or not r.content[:5].startswith(b"%PDF") and "pdf" not in ctype:
            return None                                 # not a PDF -> no cache entry
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(r.content)
        tmp.replace(path)                               # atomic: only a complete file lands
        return path
    except requests.RequestException:
        return None


def extract_text(path: Path, max_chars: int = MAX_DOC_CHARS) -> tuple[str, bool, str]:
    """(text, readable, note). No OCR: a scanned PDF returns readable=False."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        parts, total = [], 0
        for page in reader.pages:
            t = (page.extract_text() or "").strip()
            if t:
                parts.append(t)
                total += len(t)
            if total >= max_chars:
                break
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(parts)).strip()
        if len(text) < 200:
            return "", False, "scanned or image-only PDF — could not be machine-read"
        return text[:max_chars], True, ""
    except Exception as exc:                            # noqa: BLE001
        return "", False, f"could not read PDF ({exc})"


def _load_text(docs: list[Doc], sess: requests.Session) -> None:
    """Fill each Doc.text in priority order until the context budget is spent."""
    order = {"Earnings call transcript": 0, "Investor presentation": 1, "Annual report": 2}
    budget = MAX_CONTEXT_CHARS
    for d in sorted(docs, key=lambda x: order.get(x.kind, 9)):
        if budget <= 0:
            break
        path = download_pdf(d.url, sess)
        if path is None:
            d.readable, d.note = False, "document could not be downloaded"
            continue
        text, ok, note = extract_text(path, min(MAX_DOC_CHARS, budget))
        d.text, d.readable, d.note = text, ok, note
        budget -= len(text)


# --------------------------------------------------------------------------
# 4. grounded LLM brief
# --------------------------------------------------------------------------
_SYSTEM = (
    "You are an equity research assistant writing a factual company brief. "
    "You are given extracts from a company's OWN official filings (annual report, "
    "investor presentation, earnings-call transcript). Use ONLY these extracts. "
    "Never use outside or general knowledge. If the extracts do not support a "
    "point, write exactly: Not stated in the available filings. "
    "Do not infer or invent management plans, targets or initiatives. "
    "Tag each bullet with its basis in square brackets: [Management stated], "
    "[Filing disclosed], or [AI interpretation] (use [AI interpretation] only for "
    "the 'Why investors should care' synthesis, and keep it clearly grounded). "
    "Return ONLY compact JSON with keys core_focus, key_initiatives, why_care — "
    "each a single HTML string of 2-4 <div class=\"bl\">…</div> bullets."
)


def _context(docs: list[Doc]) -> str:
    blocks = []
    for d in docs:
        if d.text:
            blocks.append(f"=== SOURCE: {d.kind} ({d.date}) ===\n{d.text}")
    return "\n\n".join(blocks)


def _fingerprint(docs: list[Doc]) -> str:
    key = "|".join(sorted(f"{d.kind}:{d.date}:{d.url}" for d in docs))
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def _brief_cache_path(symbol: str, fp: str) -> Path:
    return CACHE_DIR / f"brief_{symbol}_{fp}.json"


def build_brief(company: str, config: LLMConfig) -> Brief:
    """Full pipeline. Always returns a Brief; never raises."""
    b = Brief(company=company)
    ident = resolve_identity(company)
    if not ident:
        b.unavailable = ("No public NSE-listed match for this company, so official "
                         "filings could not be located.")
        return b
    b.identity = ident

    sess = _session()
    try:
        docs = discover_documents(ident["nse_symbol"], sess)
    except Exception as exc:                            # noqa: BLE001
        docs, b.error = [], f"document discovery failed ({exc})"
    if not docs:
        b.unavailable = ("No official filings (annual report / presentation / "
                         "earnings call) could be found for this company.")
        return b

    # brief cache by document fingerprint (only regenerates on a new filing)
    fp = _fingerprint(docs)
    cache_file = _brief_cache_path(ident["nse_symbol"], fp)
    _load_text(docs, sess)
    b.docs = docs

    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            b.core_focus = cached["core_focus"]
            b.key_initiatives = cached["key_initiatives"]
            b.why_care = cached["why_care"]
            return b
        except Exception:                               # noqa: BLE001
            pass                                        # fall through and regenerate

    context = _context(docs)
    if not context:
        b.error = "The latest filings could not be machine-read (see source links)."
        return b
    if not config.is_live and not any(c.is_live for c in config.fallbacks):
        b.offline = True
        return b

    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS]
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": (
            f"Company: {ident['name']} (NSE: {ident['nse_symbol']}).\n\n"
            "Write the three sections from the extracts below.\n"
            "core_focus: what the company primarily does, its segments and where "
            "revenue/earnings come from.\n"
            "key_initiatives: concrete current initiatives the documents state "
            "(capex, expansion, new capacity/products, acquisitions, partnerships, "
            "debt reduction).\n"
            "why_care: the few factors from this commentary that most affect future "
            "growth, profitability, cash flow, valuation or risk.\n\n"
            f"EXTRACTS:\n{context}")},
    ]
    try:
        raw = post(config, messages)
        data = _extract_json(raw)
        b.core_focus = data.get("core_focus", "")
        b.key_initiatives = data.get("key_initiatives", "")
        b.why_care = data.get("why_care", "")
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({
            "core_focus": b.core_focus, "key_initiatives": b.key_initiatives,
            "why_care": b.why_care}), encoding="utf-8")
    except Exception as exc:                            # noqa: BLE001
        b.error = f"the brief could not be generated ({exc})"
    return b


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no JSON in model output")
    return json.loads(m.group(0))
