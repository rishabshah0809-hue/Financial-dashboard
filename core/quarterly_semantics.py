"""
quarterly_semantics.py
----------------------
The *document-agnostic* brains of quarterly ingestion, kept free of any PDF
library so every rule here is a pure function that can be unit-tested on plain
strings and dicts.

It answers the questions that vary company-to-company:

  * what date is this column header?          -> parse_date_token
  * is a column a quarter, an annual, a TTM,
    or a cumulative (9M/half-year) period?     -> classify_period_type
  * what unit is the statement in?             -> detect_unit / to_crore
  * how many implied decimals do the numbers
    use, and what is this cell's value?        -> detect_decimals / parse_amount
  * what canonical P&L line is this row?        -> match_row_label
  * how is each ratio defined?                  -> RATIO_REGISTRY

Nothing here assumes a page number, an x/y coordinate, a specific company, or a
specific quarter. `core/quarterly_pdf.py` supplies the geometry and OCR; this
module supplies the meaning.
"""

from __future__ import annotations

import re
from datetime import date

# ==========================================================================
# dates & periods
# ==========================================================================
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAME = {v: k.capitalize() for k, v in MONTHS.items()}
# tolerate a few common OCR manglings of month names
_MONTH_FIX = {"mareh": "mar", "jvne": "jun", "jnne": "jun", "marcb": "mar",
              "septernber": "sep", "decernber": "dec", "novernber": "nov"}

_MONTH_RE = re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*",
                       re.IGNORECASE)
_DMY_NUMERIC = re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})\b")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_YEAR2_RE = re.compile(r"['`’](\d{2})\b")


def _month_end(y: int, m: int, day: int | None) -> date:
    d = day if day else (31 if m in (1, 3, 5, 7, 8, 10, 12) else 30 if m != 2 else 28)
    for cand in (d, 31, 30, 29, 28):
        try:
            return date(y, m, min(cand, 31))
        except ValueError:
            continue
    return date(y, m, 28)


def parse_date_token(text: str) -> date | None:
    """Parse a reporting-period header into a date, order-agnostically.

    Handles day-first ("30th Jun'26", "31 Mar 2026"), month-first
    ("June 30, 2026", "Mareh 31,2026"), and dotted numeric ("30.06.2026",
    "31/03/2026"). Returns None when no plausible date is present.
    """
    if not text:
        return None
    raw = str(text).strip()
    low = raw.lower()
    for bad, good in _MONTH_FIX.items():
        low = low.replace(bad, good)

    # 0) "Q1 FY27" / "Q3 FY2026" -> the quarter's end date (Indian FY, Apr-Mar)
    qm = re.search(r"\bq([1-4])\s*fy\s*['`’]?(\d{2,4})\b", low)
    if qm:
        qnum = int(qm.group(1))
        fy = int(qm.group(2))
        fy = fy + 2000 if fy < 100 else fy
        end_month = [6, 9, 12, 3][qnum - 1]          # Q1->Jun ... Q4->Mar
        end_year = fy - 1 if qnum <= 3 else fy        # Q4 (Jan-Mar) is in fy itself
        return _month_end(end_year, end_month, None)

    # 1) dotted / slashed numeric d-m-y (Indian order)
    m = _DMY_NUMERIC.search(low)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        if 1 <= mo <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100:
            return _month_end(y, mo, d)
        # maybe m-d-y
        if 1 <= d <= 12 and 1 <= mo <= 31:
            return _month_end(y, d, mo)

    # 2) month-name based (order-agnostic)
    mon = _MONTH_RE.search(low)
    if not mon:
        return None
    month = MONTHS[mon.group(1)[:3]]
    y4 = _YEAR_RE.search(low)
    y2 = _YEAR2_RE.search(low)
    if y4:
        year = int(y4.group(0))
    elif y2:
        year = 2000 + int(y2.group(1))
    else:
        # a year must be explicit (4-digit, or apostrophe 2-digit handled above);
        # a bare "June 30" is NOT a year, so we refuse rather than invent 2030.
        return None
    # day = an integer 1..31 that is not the (2-digit) year
    day = None
    for n in re.findall(r"\d{1,4}", low):
        v = int(n)
        if 1 <= v <= 31 and v != (year % 100) and len(n) <= 2:
            day = v
            break
    if not 1900 <= year <= 2100:
        return None
    return _month_end(year, month, day)


def period_label(d: date) -> str:
    """Canonical display label, e.g. 'Jun-2026'."""
    return f"{_MONTH_NAME[d.month]}-{d.year}"


def fy_quarter_tag(d: date) -> str:
    """Indian FY (Apr-Mar) quarter tag, e.g. Jun-2026 -> 'Q1 FY27'."""
    q = ((d.month - 4) % 12) // 3 + 1
    fy = d.year + 1 if d.month >= 4 else d.year
    return f"Q{q} FY{fy % 100:02d}"


# period-type classification from a header/label snippet ---------------------
_CUMULATIVE = ("nine month", "nine-month", "9 month", "9month", "9m",
               "six month", "six-month", "half year", "half-year", "h1", "h2",
               "year to date", "year-to-date", "ytd", "period ended")
_TTM = ("ttm", "trailing", "twelve month", "twelve-month", "12 month", "12m", "ltm")
_ANNUAL = ("year ended", "year end", "annual", "full year", "fy ", "financial year")


def classify_period_type(header_text: str) -> str:
    """Return 'quarter' | 'annual' | 'ttm' | 'cumulative' | ''.

    Only a *single* quarter is usable for quarterly analysis; annual, TTM and
    cumulative (nine-month / half-year / YTD) columns are classified so callers
    can exclude them rather than mistake them for a quarter.
    """
    t = re.sub(r"\s+", " ", (header_text or "").lower())
    if not t:
        return ""
    if any(k in t for k in _TTM):
        return "ttm"
    if any(k in t for k in _CUMULATIVE):
        # "period ended" alone is ambiguous; only cumulative if paired w/ months
        if "period ended" in t and not re.search(r"month|9m|half|ytd|six|nine", t):
            pass
        else:
            return "cumulative"
    if any(k in t for k in _ANNUAL):
        return "annual"
    if "quarter" in t:
        return "quarter"
    return ""


# ==========================================================================
# units
# ==========================================================================
# unit token -> multiplier that converts the printed number into ₹ crore.
_UNIT_TO_CRORE = {
    "crore": 1.0, "cr": 1.0, "crores": 1.0,
    "lakh": 0.01, "lakhs": 0.01, "lac": 0.01, "lacs": 0.01, "laks": 0.01,
    "million": 0.1, "mn": 0.1, "millions": 0.1,
    "billion": 100.0, "bn": 100.0,
    "thousand": 1e-5, "thousands": 1e-5, "000": 1e-5,
}
_UNIT_RE = re.compile(
    r"(?:rs\.?|inr|₹|rupees)?\s*(?:in\s*)?"
    r"(crores?|lakhs?|lacs?|laks|millions?|billions?|thousands?|cr|mn|bn)\b",
    re.IGNORECASE,
)


def detect_unit(text: str) -> tuple[str, float, bool]:
    """Detect the statement's money unit from a caption like '₹ in Lakhs'.

    Returns (raw_unit_label, multiplier_to_crore, known). When no unit phrase is
    found, returns ('unknown', 1.0, False) so the caller can flag low confidence
    rather than silently assuming crore.
    """
    if not text:
        return "unknown", 1.0, False
    m = _UNIT_RE.search(text)
    if not m:
        return "unknown", 1.0, False
    token = m.group(1).lower()
    mult = _UNIT_TO_CRORE.get(token) or _UNIT_TO_CRORE.get(token.rstrip("s"), 1.0)
    canonical = {
        "cr": "crore", "crores": "crore", "crore": "crore",
        "lakh": "lakh", "lakhs": "lakh", "lac": "lakh", "lacs": "lakh", "laks": "lakh",
        "mn": "million", "million": "million", "millions": "million",
        "bn": "billion", "billion": "billion", "billions": "billion",
        "thousand": "thousand", "thousands": "thousand",
    }.get(token, token)
    return canonical, mult, True


def to_crore(value: float | None, multiplier: float) -> float | None:
    return None if value is None else value * multiplier


# ==========================================================================
# numbers
# ==========================================================================
def detect_decimals(tokens: list[str]) -> int:
    """Infer the document's decimal convention from tokens that show a decimal.

    Indian statements group with commas and use '.' for decimals, so the count
    of digits after the last '.' among decimal-bearing tokens is the convention
    (usually 0 for integer-crore filings, 2 for paise). Returns 0 when no token
    carries an explicit decimal.
    """
    from collections import Counter
    counts: Counter = Counter()
    for t in tokens:
        s = str(t)
        if "." in s:
            frac = re.sub(r"[^0-9]", "", s.rsplit(".", 1)[1])
            # a 1-2 digit tail is a decimal (paise); a 3-digit tail is a
            # thousands group (OCR often renders the comma as a dot).
            if 0 < len(frac) <= 2:
                counts[len(frac)] += 1
    return counts.most_common(1)[0][0] if counts else 0


def parse_amount(token: str, decimals: int = 0) -> float | None:
    """Parse one money cell, applying the per-document decimal convention.

    * A token with an explicit '.' is read as a decimal (commas are grouping).
    * A token with no '.' is read as digits; when the document uses N implied
      decimals, the last N digits are the fractional part (handles a text layer
      that dropped the separator, e.g. '3446061' -> 34460.61 at N=2).
    Parentheses or a leading '-' mean negative. Returns None if not a number.
    """
    if token is None:
        return None
    s = str(token).strip()
    if not s or s in {"-", "--", ".", "—"}:
        return None
    neg = ("(" in s and ")" in s) or s.lstrip().startswith("-")

    if decimals <= 0:
        # integer document: every '.' , ',' etc. is grouping/OCR noise.
        digits = re.sub(r"[^0-9]", "", s)
        if not digits:
            return None
        val = float(digits)
        return -val if neg else val

    # decimal document (e.g. paise/lakh): the fractional part is the last '.'
    # group when it has 1-2 digits; otherwise the '.' is grouping and the last
    # N digits are the implied decimals (text layer dropped the separator).
    s2 = re.sub(r"[^0-9.]", "", s)
    if not s2 or s2 == ".":
        return None
    if "." in s2:
        intpart, frac = s2.rsplit(".", 1)
        intpart = intpart.replace(".", "")
        if 1 <= len(frac) <= 2:
            try:
                val = float(f"{intpart or 0}.{frac}")
            except ValueError:
                return None
        else:                                   # 3+ tail -> grouping, all integer
            digits = intpart + frac
            val = float(digits) / (10 ** decimals) if digits else None
            if val is None:
                return None
    else:
        val = float(s2) / (10 ** decimals)
    return -val if neg else val


def parse_ratio(token: str) -> float | None:
    """Parse a printed ratio / EPS where the '.' is always a real decimal
    (e.g. '0.39', '1.12', '25.13'). Commas are grouping. Never strips the dot."""
    if token is None:
        return None
    s = str(token).strip()
    if not s or s in {"-", "--", "—"}:
        return None
    neg = ("(" in s and ")" in s) or s.lstrip().startswith("-")
    s2 = re.sub(r"[^0-9.]", "", s)
    if not s2 or s2 == ".":
        return None
    if s2.count(".") > 1:                        # keep only the last dot
        head, tail = s2.rsplit(".", 1)
        s2 = head.replace(".", "") + "." + tail
    try:
        val = float(s2)
    except ValueError:
        return None
    return -val if neg else val


# ==========================================================================
# row-label synonyms (semantic mapping, not scattered ifs)
# ==========================================================================
# canonical FinancialModel line -> ordered regex synonyms (normalised, lower).
# Order matters: the first canonical whose pattern matches wins, so more
# specific lines are listed before the generic ones they could be confused with.
ROW_SYNONYMS: list[tuple[str, tuple[str, ...]]] = [
    ("Value of Sales & Services", (r"value of sales",)),
    ("GST Recovered",             (r"gst recovered", r"less.*gst")),
    # revenue / sales (net of GST): "revenue from operations", "net sales", banks' "interest earned"
    ("Sales",                     (r"revenue from operations", r"^revenue from",
                                   r"\bnet sales\b", r"income from operations",
                                   r"interest earned")),
    ("Other Income",              (r"o\w{0,2}her income",)),
    ("Total Income",              (r"^total income", r"^total revenue")),
    ("Cost of Materials Consumed",(r"cost of material",)),
    ("Purchases of Stock-in-Trade",(r"purchases? of stock",)),
    ("Changes in Inventories",    (r"changes? in inventor",)),
    ("Cost of Food and Beverages",(r"cost of food",)),
    ("Excise Duty",               (r"excise du",)),
    ("Employee Benefits Expense", (r"employee benefit", r"employee cost", r"employee expense")),
    # EBITDA-equivalent line must be matched BEFORE 'Finance Costs', because its
    # label contains the substring "finance cost" ("...before ... finance cost ...").
    ("Operating Profit (reported)", (r"profit before depreciation.*finance",
                                     r"earnings before interest.*depreciation",
                                     r"operating profit before", r"\bebitda\b")),
    ("Finance Income",            (r"finance income",)),
    ("Finance Costs",             (r"finance cost", r"interest.*finance",
                                   r"finance charge", r"interest expense")),
    ("Depreciation",              (r"depreciation", r"\bd\s*&\s*a\b")),
    ("Other Expenses",            (r"other expense",)),
    ("Total Expenses",            (r"total expense", r"total expenditure")),
    ("Exceptional Items",         (r"exceptional item",)),
    ("Earnings Before Tax",       (r"profit before tax", r"profit/.*before tax", r"\bpbt\b")),
    ("Current Tax",               (r"current\s*tax",)),
    ("Deferred Tax",              (r"deferred\s*tax",)),
    ("Tax Expense",               (r"^tax expense", r"income tax expense", r"^taxation")),
    # Net profit / PAT (bottom line). Checked before the plain "profit after tax"
    # line so a "...and share" / "net profit after tax" label is not mistaken for it.
    # bottom-line PAT. Kept specific so a sub-line like "Net Profit attributable
    # to: Owners of the Company" is NOT mistaken for the total.
    ("Net Profit",                (r"profit after tax and share",
                                   r"net profit after tax",
                                   r"profit for the (period|quarter)(?! attribut)",
                                   r"profit/\(loss\) for the (period|quarter)")),
    ("Share of Profit of Associates", (r"share of profit.{0,40}associat",
                                       r"share of .{0,20}associat")),
    ("Profit After Tax",          (r"pro[a-z]{0,3}t after tax\b(?!.*share)",)),
    ("Total Comprehensive Income",(r"total comprehensive income for",)),
]


def normalize_label(label: str) -> str:
    s = re.sub(r"\s+", " ", str(label)).strip().lower()
    s = s.replace("&", " and ")
    return re.sub(r"\s+", " ", s)


def match_row_label(label: str) -> str | None:
    """Map a results-table row label to a canonical FinancialModel line, or None."""
    n = normalize_label(label)
    if not n:
        return None
    for canon, pats in ROW_SYNONYMS:
        for p in pats:
            if re.search(p, n):
                return canon
    return None


# ==========================================================================
# ratio registry (one definition per ratio; Python-first)
# ==========================================================================
# inputs reference canonical raw line names OR the derived intermediates
# 'EBITDA'/'EBIT' produced by derive. 'direction' feeds quarterly scoring.
RATIO_REGISTRY: dict[str, dict] = {
    "EBITDA Margin": {
        "inputs": ["EBITDA", "Sales"], "formula": "EBITDA / Revenue from Operations",
        "kind": "margin", "direction": "higher", "unit": "%"},
    "EBIT Margin": {
        "inputs": ["EBIT", "Sales"], "formula": "EBIT / Revenue from Operations",
        "kind": "margin", "direction": "higher", "unit": "%"},
    "Net Profit Margin": {
        "inputs": ["Net Profit", "Sales"], "formula": "Net Profit / Revenue from Operations",
        "kind": "margin", "direction": "higher", "unit": "%"},
    "Interest % Sales": {
        "inputs": ["Finance Costs", "Sales"], "formula": "Finance Costs / Revenue",
        "kind": "margin", "direction": "lower", "unit": "%"},
    "Depreciation % Sales": {
        "inputs": ["Depreciation", "Sales"], "formula": "Depreciation / Revenue",
        "kind": "margin", "direction": "lower", "unit": "%"},
    "Tax Payout %": {
        "inputs": ["Tax", "Earnings Before Tax"],
        "formula": "(Current Tax + Deferred Tax) / PBT",
        "kind": "margin", "direction": "neutral", "unit": "%"},
    "Interest Coverage Ratio": {
        "inputs": ["EBIT", "Finance Costs"], "formula": "(PBT + Finance Costs) / Finance Costs",
        "kind": "ratio", "direction": "higher", "unit": "x",
        "validate_against": "Interest Coverage Ratio"},
    "Sales Growth": {
        "inputs": ["Sales"], "formula": "Sales_current / Sales_previous - 1",
        "kind": "growth", "direction": "higher", "unit": "%"},
    "Net Profit Growth": {
        "inputs": ["Net Profit"], "formula": "Net Profit_current / Net Profit_previous - 1",
        "kind": "growth", "direction": "higher", "unit": "%"},
}

# metrics that need a balance sheet / cash flow the results table lacks; these
# are the PDF-reported-or-unavailable set (never Python-derived from a P&L).
REPORTED_ONLY_RATIOS = ("Debt to Equity Ratio", "Current Ratio", "Debt to Asset Ratio",
                        "Debtor Turnover Ratio", "Inventory Turnover")
NO_PDF_INPUT_RATIOS = ("Return on Equity (ROE) %", "Return on Capital Employed (ROCE) %",
                       "Return on Assets (ROA) %", "Cash Conversion Cycle",
                       "CFO / PAT", "Fixed Asset Turnover")

PERCENT_METRICS = {name for name, d in RATIO_REGISTRY.items() if d["unit"] == "%"}


# ==========================================================================
# metric applicability by business type
# ==========================================================================
# metrics that are not meaningful for a given business type -> shown as "—"
NOT_APPLICABLE: dict[str, set[str]] = {
    "banking": {"Return on Capital Employed (ROCE) %", "Fixed Asset Turnover",
                "Inventory Turnover", "Cash Conversion Cycle", "EBITDA Margin"},
    "financial_services": {"Return on Capital Employed (ROCE) %", "Fixed Asset Turnover",
                           "Inventory Turnover", "Cash Conversion Cycle", "EBITDA Margin"},
    "insurance": {"Return on Capital Employed (ROCE) %", "Fixed Asset Turnover",
                  "Inventory Turnover", "Cash Conversion Cycle"},
}


def metric_applicable(metric: str, business_type: str | None) -> bool:
    if not business_type:
        return True
    return metric not in NOT_APPLICABLE.get(business_type, set())
