"""
interpret.py
------------
Dynamic Financial Model Interpretation.

When a workbook is loaded, FundaCheck reads ONLY the numbers already parsed from
that Excel model (income statement, balance sheet, cash flow, common-size,
ratios) and asks the existing LLM layer to write a short, deep interpretation of
what those numbers say about THIS company — never a generic template.

Design goals that shape every choice here:

  * The uploaded model is the PRIMARY (and effectively only) source of figures.
    The compact payload is built from the already-parsed frames, so the workbook
    is never re-parsed and raw cells are never shipped to the model.
  * Token frugality. Only a curated set of metrics, and only the most recent
    handful of periods, are sent — a payload that fits comfortably inside the
    Groq / Gemini free tiers, generated in ONE call.
  * Company-specific prioritisation. The detected sector is passed so the model
    weights the ratios that matter for a bank differently from an IT firm.
  * It degrades gracefully: any failure leaves the Ask-AI chat fully working and
    shows a plain "temporarily unavailable" note rather than a fabricated one.

The interpretation is generated ONCE per distinct workbook (see `fingerprint`);
the caller memoises it in session state so tab switches and reruns reuse it.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .llm import LLMConfig, post, _extract_json
from .parser import FinancialModel

# The seven sections, in display order: (json key, heading).
SECTIONS: list[tuple[str, str]] = [
    ("growth", "Growth"),
    ("margins", "Margins"),
    ("costs", "Costs"),
    ("returns", "Returns"),
    ("efficiency", "Efficiency & Working Capital"),
    ("leverage_cashflow", "Leverage & Cash Flow"),
    ("valuation", "Valuation"),
]

# How many of the most-recent periods to ship. Enough to show a trend without
# blowing the free-tier token budget on a decade of history.
MAX_PERIODS = 8

# Format hints per metric group. 'pct' values are stored as decimals in the
# workbook (0.1456 -> 14.6%); 'cr' are rupee-crore figures; 'x' are plain
# ratios/multiples; 'days' are day counts.
#
# Each entry: (display label, alias tuple, format). First alias found wins, so
# a workbook that names a line slightly differently still resolves.
_GROWTH_SCALE = [
    ("Sales", ("Sales", "Revenue", "Net Sales"), "cr"),
    ("Net Profit", ("Net Profit", "Net profit", "PAT"), "cr"),
    ("EBITDA", ("EBITDA",), "cr"),
    ("EBIT", ("EBIT (OPM)", "EBIT", "Operating Profit"), "cr"),
    ("EPS", ("Earnings per Share", "EPS"), "x"),
]
_MARGINS = [
    ("Gross Margin", ("Gross Margin", "Gross Margin % Sales"), "pct"),
    ("EBITDA Margin", ("EBITDA Margin", "EBITDA Margins"), "pct"),
    ("EBIT Margin", ("EBIT Margin", "EBIT Margins"), "pct"),
    ("Net Profit Margin", ("Net Profit Margin", "Net Margins"), "pct"),
    ("Other Income % Sales", ("Other income % Sales", "Other Income % Sales"), "pct"),
]
_COSTS = [
    ("COGS % Sales", ("COGS % Sales",), "pct"),
    ("S&G Expenses % Sales", ("Selling & General Expenses % Sales", "S&G Exp % Sales"), "pct"),
    ("Depreciation % Sales", ("Depreciation%Sales", "Depreciation % Sales"), "pct"),
    ("Interest % Sales", ("Interest % Sales",), "pct"),
    ("Effective Tax Rate", ("Effective Tax Rate", "Tax Payout %"), "pct"),
]
_RETURNS = [
    ("ROE", ("Return on Equity (ROE) %", "ROE"), "pct"),
    ("ROCE", ("Return on Capital Employed (ROCE) %", "ROCE"), "pct"),
    ("ROIC", ("Return on Invested Capital (ROIC) %", "ROIC"), "pct"),
    ("ROA", ("Return on Assets (ROA) %", "ROA"), "pct"),
]
_EFFICIENCY = [
    ("Fixed Asset Turnover", ("Fixed Asset Turnover",), "x"),
    ("Capital Turnover", ("Capital Turnover Ratio", "Capital Turnover"), "x"),
    ("Inventory Days", ("Inventory Days",), "days"),
    ("Debtor Days", ("Debtor Days",), "days"),
    ("Payable Days", ("Payable Days",), "days"),
    ("Cash Conversion Cycle", ("Cash Conversion Cycle",), "days"),
    ("Receivables", ("Receivables", "Trade Receivables", "Debtors"), "cr"),
    ("Inventory", ("Inventory", "Inventories"), "cr"),
]
_LEVERAGE_CF = [
    ("Borrowings", ("Borrowings", "Total Debt"), "cr"),
    ("Debt to Equity", ("Debt to Equity Ratio", "Debt to Equity"), "x"),
    ("Interest Coverage", ("Interest Coverage Ratio",), "x"),
    ("Interest", ("Interest", "Finance Cost"), "cr"),
    ("CFO", ("Cash from Operating Activity", "Cash from Operations"), "cr"),
    ("CFO / PAT", ("CFO / PAT",), "x"),
    ("Investing Cash Flow", ("Cash from Investing Activity",), "cr"),
    ("Financing Cash Flow", ("Cash from Financing Activity",), "cr"),
    ("Net Block (fixed assets)", ("Net Block", "Fixed Assets"), "cr"),
]
_VALUATION = [
    ("P/E", ("PE Ratio", "P/E", "Price to Earnings"), "x"),
    ("Price / Sales", ("Price to Sales", "Price / Sales"), "x"),
    ("P/B", ("Price to Book", "P/B", "Price to Book Value"), "x"),
    ("EV / EBITDA", ("EV/EBITDA", "EV / EBITDA"), "x"),
    ("PEG", ("PEG Ratio", "PEG"), "x"),
]

_GROUPS: list[tuple[str, list]] = [
    ("GROWTH & SCALE (Rs crore unless noted)", _GROWTH_SCALE),
    ("MARGINS", _MARGINS),
    ("COST STRUCTURE (% of sales)", _COSTS),
    ("RETURNS", _RETURNS),
    ("EFFICIENCY & WORKING CAPITAL", _EFFICIENCY),
    ("LEVERAGE & CASH FLOW (Rs crore unless noted)", _LEVERAGE_CF),
    ("VALUATION", _VALUATION),
]


@dataclass
class Interpretation:
    company: str
    sector: str
    sections: dict[str, list[str]] = field(default_factory=dict)  # key -> bullets
    sources: list[str] = field(default_factory=list)
    offline: bool = False          # no live provider key detected
    error: str = ""                # user-facing soft error note
    error_detail: str = ""         # raw provider error (logs only)
    fingerprint: str = ""
    payload_text: str = ""         # the compact context actually sent (debug)
    usage: dict = field(default_factory=dict)   # rough token estimates


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------
def _fmt(value: float, kind: str) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    if kind == "pct":
        return f"{value * 100:.1f}%"
    if kind == "days":
        return f"{value:.0f}d"
    if kind == "cr":
        return f"{value:,.0f}"
    return f"{value:.2f}"          # 'x' / 'num'


def _clean_series(model: FinancialModel, aliases: tuple[str, ...]) -> pd.Series:
    """First non-empty alias, numeric, infinities/NaN dropped, chronological."""
    for name in aliases:
        s = model.series(name)
        if not s.empty:
            s = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
            if not s.empty:
                return s
    return pd.Series(dtype="float64")


def _period_columns(model: FinancialModel) -> list[str]:
    cols = list(model.years)
    return cols[-MAX_PERIODS:] if len(cols) > MAX_PERIODS else cols


# --------------------------------------------------------------------------
# compact payload
# --------------------------------------------------------------------------
def build_payload(model: FinancialModel, sector: str) -> str:
    """Turn the parsed frames into a small, LLM-ready context block.

    Only metrics actually present are emitted, and only the most recent periods,
    so the same builder yields a tight payload whether the model is a bank or a
    steel maker."""
    periods = _period_columns(model)
    lines: list[str] = [
        f"COMPANY: {model.company.title()}",
        f"SECTOR (detected): {sector}",
        f"PERIODS: {', '.join(periods)}",
        "",
        "All figures are taken directly from the uploaded Excel model.",
    ]

    for group_title, group in _GROUPS:
        rows: list[str] = []
        for label, aliases, kind in group:
            s = _clean_series(model, aliases)
            if s.empty:
                continue
            vals = [(_fmt(s.get(p, np.nan), kind) if p in s.index else "n/a")
                    for p in periods]
            # Skip a line that is entirely n/a for the shown window.
            if all(v == "n/a" for v in vals):
                continue
            pairs = "  ".join(f"{p} {v}" for p, v in zip(periods, vals))
            rows.append(f"  {label}: {pairs}")
        if rows:
            lines.append("")
            lines.append(f"[{group_title}]")
            lines.extend(rows)

    return "\n".join(lines)


def fingerprint(model: FinancialModel, sector: str) -> str:
    """A stable hash of the workbook's numbers + detected sector.

    Same file (same numbers) -> same fingerprint -> reuse the interpretation.
    A different model, or an edited one, changes the numbers and the hash, which
    triggers exactly one regeneration."""
    payload = build_payload(model, sector)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# the LLM prompt
# --------------------------------------------------------------------------
_SYSTEM = """You are a buy-side equity analyst interpreting ONE company's own \
financial model. You are handed a compact set of figures taken directly from an \
uploaded Excel workbook (income statement, balance sheet, cash flow, common-size \
and ratio lines) across several periods.

Absolute rules:
- The workbook numbers are the PRIMARY source. Use the actual values and trends \
you are given. NEVER invent a figure that is not in the data.
- Do NOT write generic textbook lines like "revenue growth is positive, which is \
good." For every point, explain WHY the numbers moved and what the RELATIONSHIP \
between numbers implies (e.g. gross margin up but EBIT margin down => a cost line \
below gross profit is consuming the gain).
- Connect multiple numbers. "ROCE fell 8%->4% while capital employed rose sharply, \
so the capital base grew faster than operating profit" — not "ROCE fell to 4%".
- Name the ROOT CAUSE and its DOWNSTREAM effect where the numbers support it: call \
out the single biggest structural shift, what it drove, and what it leaves exposed \
("the root cause of nearly every downstream profitability gain"; "a leading sign \
the company invested heavily and is now absorbing the cost").

STYLE — every bullet MUST follow this shape:
  **<Metric> — <short descriptor> (<the key figures or moves>):** <one or two \
sentences of causal explanation>.
The bold lead-in (between ** **) carries the label, a terse descriptor and the \
numbers in parentheses; the sentence after the colon explains the WHY and the \
relationship. Example of the required voice:
  "**Net Margin still rose in FY26 (7.7% -> 9.6%) despite the EBIT dip:** the gap \
is filled by Other Income jumping to 11.6% of sales, so the record bottom line is \
propped up by non-operating income, not the core business."
- GROWTH section specifically: express the trajectory as year-on-year PERCENT \
moves that you compute from the figures given (e.g. "-8.9% FY21 -> +75.6% FY22 -> \
+83.7% FY23 -> -24.4% FY24 -> ~flat FY25-26"), then explain what each swing \
reflects and whether growth is off a low base, organic, or settling onto a larger \
base. Compute the percentages from the absolute values in the payload; do not \
invent them.
- Prioritise by BUSINESS TYPE. First infer the business from the company and \
sector, then weight the ratios that matter for it: a bank lives on ROA / spread / \
leverage-as-model and working-capital days are near meaningless; an IT firm on \
margins, asset-light returns and employee cost; a capital-intensive industrial on \
depreciation, capex, asset turnover, ROCE and leverage.
- Do not force bullets. If a section has nothing material, return the single \
string "Nothing material changed in this area." Target counts: Growth 2-3, \
Margins 2-3, Costs 1-3, Returns 1-3, Efficiency 1-3, Leverage & Cash Flow 2-4, \
Valuation 1-2. If valuation lines are absent, return \
"Valuation data not available in the model."
- This is analysis, not advice. Never say buy/sell/guaranteed. Use analytical \
language: supports, indicates, could suggest, raises a concern, worth monitoring.

External context (OPTIONAL, SECONDARY): you MAY add at most 0-3 short, \
well-established sector facts from general market knowledge — only when a fact the \
numbers cannot explain on their own genuinely helps (is the industry cyclical? is \
a commodity price cycle relevant? is negative working capital normal for this \
model? is leverage unusual for the sector?). If, and only if, you use such a fact, \
append at the END of that section a single line: "External context: <source>" \
(e.g. Reuters, Screener, Morningstar, Moneycontrol, Zerodha Varsity). Never attach \
a source to a point that came from the workbook numbers. Never claim a source you \
did not actually draw on. Most sections will have no external line.

Return STRICT JSON, no markdown fencing, with exactly these keys:
{
  "growth": [ "bullet", ... ]            // or the single "Nothing material..." string
  "margins": [ ... ],
  "costs": [ ... ],
  "returns": [ ... ],
  "efficiency": [ ... ],
  "leverage_cashflow": [ ... ],
  "valuation": [ ... ],
  "sources": [ "only sources actually used across the analysis" ]
}
Each bullet is one plain-text string. You may mark a short lead-in with **double \
asterisks** for emphasis; use no other markup. A section value may instead be a \
single string when it is the "Nothing material..." / "Valuation data not \
available in the model." fallback."""


def _build_messages(payload: str) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": (
            "Interpret the following model. Follow the section structure and the "
            "rules exactly. Keep every point specific to these numbers.\n\n"
            + payload)},
    ]


def _coerce_section(value) -> list[str]:
    """Normalise a section into a list of bullet strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                # tolerate {"point": "..."} shapes
                text = item.get("point") or item.get("text") or ""
                if str(text).strip():
                    out.append(str(text).strip())
        return out
    return [str(value)]


def _approx_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) — for the UI only, never billed."""
    return max(1, len(text) // 4)


# Substrings that mark a *transient* failure worth retrying (free-tier congestion,
# a provider blip, a rate-limit window, or a truncated/non-JSON reply). An auth or
# bad-request error is NOT here, so those fail fast instead of wasting retries.
_TRANSIENT = (
    "503", "502", "504", "429", "unavailable", "high demand", "overload",
    "timeout", "timed out", "temporarily", "rate", "quota", "tpm", "too many",
    "connection", "reset", "bad gateway", "no json", "json", "empty response",
    "all llm providers failed",
)
# Bounded so the Streamlit spinner never hangs: 4 attempts, backing off 1s/2s/4s.
_MAX_ATTEMPTS = 4


def _is_transient(err: str) -> bool:
    low = err.lower()
    return any(token in low for token in _TRANSIENT)


# A lighter Gemini alias that stays responsive when the standard flash model is
# congested (free-tier 503s). Appended as a LAST-RESORT fallback only, reusing the
# same key, so it never changes the primary provider order the app chose.
_GEMINI_LITE_MODEL = "gemini-flash-lite-latest"


def _augment(config: LLMConfig) -> LLMConfig:
    """Return a config whose fallback chain also ends in a lighter Gemini model.

    This widens the free-tier safety net without touching core.llm: if a Gemini
    key is present anywhere in the chain, a final attempt on the lite model is
    appended. The caller's config is not mutated (session-state reuse)."""
    chain = [config, *(config.fallbacks or [])]
    gem_keys: list[str] = []
    for c in chain:
        if c.provider == "gemini" and c.api_keys:
            gem_keys = c.api_keys
            break
    if not gem_keys:
        return config
    if any(c.provider == "gemini" and c.model == _GEMINI_LITE_MODEL for c in chain):
        return config
    lite = LLMConfig(provider="gemini", api_keys=list(gem_keys), model=_GEMINI_LITE_MODEL)
    primary = LLMConfig(
        provider=config.provider, api_keys=list(config.api_keys), model=config.model,
        temperature=config.temperature, reasoning_effort=config.reasoning_effort,
        fallbacks=[*(config.fallbacks or []), lite],
    )
    return primary


def _generate(config: LLMConfig, messages: list[dict]) -> tuple[str, dict]:
    """Call the LLM and parse its JSON, retrying transient failures with backoff.

    The whole provider chain (Groq -> Gemini) is retried, because free-tier 503s
    and rate-limits clear on a second try; a persistent auth/format error breaks
    out immediately. Raises the last error only when every attempt is spent."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            raw = post(config, messages)
            return raw, _extract_json(raw)
        except Exception as exc:                       # noqa: BLE001
            last_exc = exc
            if attempt < _MAX_ATTEMPTS - 1 and _is_transient(str(exc)):
                time.sleep(2 ** attempt)               # 1s, 2s, 4s
                continue
            break
    raise last_exc if last_exc else RuntimeError("generation failed")


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------
def interpret(model: FinancialModel, sector: str, config: LLMConfig) -> Interpretation:
    """Full pipeline. Always returns an Interpretation; never raises."""
    result = Interpretation(company=model.company, sector=sector)
    payload = build_payload(model, sector)
    result.payload_text = payload
    result.fingerprint = hashlib.md5(payload.encode("utf-8")).hexdigest()

    # No live provider at all -> honest offline note, chat stays usable.
    if not config.is_live and not any(c.is_live for c in (config.fallbacks or [])):
        result.offline = True
        return result

    messages = _build_messages(payload)
    prompt_chars = sum(len(m["content"]) for m in messages)
    try:
        raw, data = _generate(_augment(config), messages)
    except Exception as exc:                       # noqa: BLE001 - surfaced in UI
        result.error = ("Financial interpretation temporarily unavailable. "
                        "Your model data is available below.")
        result.error_detail = str(exc)
        return result

    for key, _heading in SECTIONS:
        result.sections[key] = _coerce_section(data.get(key))
    srcs = data.get("sources") or []
    result.sources = [str(s).strip() for s in srcs if str(s).strip()] \
        if isinstance(srcs, list) else []

    result.usage = {
        "approx_input_tokens": _approx_tokens("".join(m["content"] for m in messages)),
        "approx_output_tokens": _approx_tokens(raw),
        "prompt_chars": prompt_chars,
    }
    return result
