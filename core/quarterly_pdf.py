"""
quarterly_pdf.py
----------------
Ingest a listed Indian company's *quarterly-results* PDF into the same
``FinancialModel`` the Excel path produces, so the rest of the dashboard keeps
working unchanged.

Source-of-truth hierarchy (the whole point of this module)
==========================================================

    PDF raw values                        ← extracted / OCR'd from the filing
            │
            ▼
    Python deterministic calculations     ← ratios computed here, in Python
            │
            ▼
    PDF-reported ratios (validation)      ← the filing's own ratio table, used
            │                               to CHECK the Python result, or as the
            │                               value only when Python cannot compute
            ▼
    Screener fallback                     ← ONLY for inputs the PDF lacks, and
            │                               ONLY if a genuinely quarterly value
            │                               exists (annual/TTM is rejected)
            ▼
    Unavailable                           ← never invented, never estimated

Every derived metric carries provenance::

    {value, period, source, formula, validation}

where ``source`` is one of::

    pdf_raw | python_derived | pdf_reported_validation | screener_fallback | unavailable

Rules enforced in code:
  * arithmetic is always Python — the LLM never computes a financial number;
  * a Screener annual/TTM value is never presented as a quarterly value;
  * the PDF's printed ratio is a *validation reference* when Python can compute
    the ratio independently, and only becomes the value when Python cannot;
  * missing data stays unavailable.

Why extraction is hybrid (text + OCR)
=====================================
In a BSE/NSE quarterly filing the consolidated P&L table's *current-quarter*
column is frequently a rasterised image — its numbers are not in the PDF text
layer at all; only the older columns come through as text. So a text-only parse
loses the very column the user cares about. We therefore read text where we can,
OCR the rasterised column, and validate everything against accounting identities
before trusting it.

The heavy libraries (pdfplumber, PyMuPDF, an OCR engine) are imported lazily so
the Excel workflow never depends on them.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

import numpy as np
import pandas as pd

from .parser import FinancialModel, ParseError

# --------------------------------------------------------------------------
# optional-dependency probes (kept lazy; see _require_libs)
# --------------------------------------------------------------------------
try:
    import pdfplumber                   # noqa: F401
    _HAVE_PDFPLUMBER = True
except Exception:                       # noqa: BLE001
    _HAVE_PDFPLUMBER = False

try:
    import pymupdf                      # noqa: F401
    _HAVE_PYMUPDF = True
except Exception:                       # noqa: BLE001
    try:
        import fitz as pymupdf          # older name  # noqa: F401
        _HAVE_PYMUPDF = True
    except Exception:                   # noqa: BLE001
        _HAVE_PYMUPDF = False

# OCR engines are resolved lazily. pytesseract (backed by the tesseract-ocr
# system package in packages.txt) is the primary/deploy path; rapidocr is a
# pure-pip fallback so the module also runs where the tesseract binary is absent
# (e.g. local dev / CI). See _ocr_column.
_OCR = None
_OCR_KIND = None
_OCR_TRIED = False


class QuarterlyPDFError(ParseError):
    """Raised when a PDF cannot be read as a quarterly-results filing.

    Subclasses ``ParseError`` so existing app-level handlers still catch it.
    """


# ==========================================================================
# provenance data model
# ==========================================================================
SOURCE_PDF_RAW = "pdf_raw"
SOURCE_PY = "python_derived"
SOURCE_PDF_REPORTED = "pdf_reported_validation"
SOURCE_SCREENER = "screener_fallback"
SOURCE_UNAVAILABLE = "unavailable"


@dataclass
class Provenance:
    """One metric's value plus where it came from and how it was checked."""
    value: float | None
    period: str
    source: str
    formula: str = ""
    validation: str = "not_available"     # pass | mismatch | not_available
    unit: str = ""                        # "%", "x", "₹ crore", ...
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "period": self.period,
            "source": self.source,
            "formula": self.formula,
            "validation": self.validation,
            "unit": self.unit,
            "note": self.note,
        }


# --------------------------------------------------------------------------
# small text/number/date helpers
# --------------------------------------------------------------------------
def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def _clean_ocr_date(text: str) -> str:
    t = re.sub(r"[^0-9A-Za-z']", " ", str(text))
    return re.sub(r"\s+", " ", t).strip()


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAME = {v: k.capitalize() for k, v in _MONTHS.items()}

_DATE_RE = re.compile(
    r"(?:(\d{1,2})\s*(?:st|nd|rd|th)?\s*)?"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"\D{0,3}['`’]?\s*(\d{2,4})",
    re.IGNORECASE,
)


def _parse_date(day: str | None, mon: str, yr: str) -> date | None:
    m = _MONTHS.get(mon.lower()[:3])
    if not m:
        return None
    y = int(yr)
    if y < 100:
        y += 2000
    d = int(day) if day else (31 if m in (3, 12) else 30)
    for cand in (d, 30, 29, 28):
        try:
            return date(y, m, min(cand, 31))
        except ValueError:
            continue
    return None


def _period_label(d: date) -> str:
    """Filing-style period label, e.g. Jun-2026 (matches the branch scaffold)."""
    return f"{_MONTH_NAME[d.month]}-{d.year}"


def _fy_quarter_tag(d: date) -> str:
    """Indian FY (Apr-Mar) quarter tag, e.g. Jun-2026 -> 'Q1 FY27'."""
    q = ((d.month - 4) % 12) // 3 + 1
    fy = d.year + 1 if d.month >= 4 else d.year
    return f"Q{q} FY{fy % 100:02d}"


def _num_int(text: Any) -> float | None:
    """Integer money cell from noisy text/OCR: strip grouping punctuation,
    parentheses/leading minus mean negative. None if not a number."""
    if text is None:
        return None
    s = str(text).strip()
    if not s or s in {"-", "--", "."}:
        return None
    neg = ("(" in s and ")" in s) or s.lstrip().startswith("-")
    digits = re.sub(r"[^0-9]", "", s)
    if not digits:
        return None
    val = float(digits)
    return -val if neg else val


def _num_dec(text: Any) -> float | None:
    """Decimal cell (EPS / a reported ratio). Keeps the last separator as the
    decimal point when followed by 1-2 digits; earlier separators are grouping.
    Conservative: returns None when the shape is ambiguous."""
    if text is None:
        return None
    s = str(text).strip().replace(" ", "")
    neg = ("(" in s and ")" in s) or s.startswith("-")
    s = re.sub(r"[()%]", "", s)
    m = re.search(r"(\d[\d.,:]*)", s)
    if not m:
        return None
    body = m.group(1)
    parts = re.split(r"[.,:]", body)
    if len(parts) == 1:
        val = float(parts[0])
    else:
        dec = parts[-1]
        if 0 < len(dec) <= 2:
            val = float("".join(parts[:-1]) + "." + dec)
        else:
            val = float("".join(parts))
    return -val if neg else val


# ==========================================================================
# OCR (engine-agnostic)
# ==========================================================================
def _resolve_ocr():
    """Pick an OCR engine once: pytesseract if its binary is present, else
    rapidocr, else None. Returns (callable_kind, engine)."""
    global _OCR, _OCR_KIND, _OCR_TRIED
    if _OCR_TRIED:
        return _OCR_KIND, _OCR
    _OCR_TRIED = True
    # 1) pytesseract + tesseract-ocr (the deployment path via packages.txt)
    try:
        import pytesseract
        pytesseract.get_tesseract_version()          # raises if binary missing
        _OCR, _OCR_KIND = pytesseract, "pytesseract"
        return _OCR_KIND, _OCR
    except Exception:                                # noqa: BLE001
        pass
    # 2) rapidocr (pure pip; no system binary) — local/CI fallback
    try:
        from rapidocr_onnxruntime import RapidOCR
        _OCR, _OCR_KIND = RapidOCR(), "rapidocr"
        return _OCR_KIND, _OCR
    except Exception:                                # noqa: BLE001
        _OCR, _OCR_KIND = None, None
        return _OCR_KIND, _OCR


def ocr_available() -> bool:
    kind, _ = _resolve_ocr()
    return kind is not None


def _ocr_column(page, x0: float, x1: float, y0: float, y1: float,
                zoom: float = 5.0) -> list[tuple[float, str]]:
    """OCR one rasterised column, returning [(y_center_in_pdf_points, text)].

    The strip is rendered on its own at high zoom so OCR has a single-column
    job; y-coordinates are mapped back to PDF points for row alignment.
    """
    kind, engine = _resolve_ocr()
    if engine is None or not _HAVE_PYMUPDF:
        return []
    import pymupdf as _pm
    from PIL import Image
    clip = _pm.Rect(x0, y0, x1, y1)
    pix = page.get_pixmap(matrix=_pm.Matrix(zoom, zoom), clip=clip)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")

    out: list[tuple[float, str]] = []
    if kind == "pytesseract":
        data = engine.image_to_data(img, config="--psm 6",
                                    output_type=engine.Output.DICT)
        n = len(data["text"])
        for i in range(n):
            txt = (data["text"][i] or "").strip()
            if not txt:
                continue
            y_center_px = data["top"][i] + data["height"][i] / 2.0
            out.append((y0 + y_center_px / zoom, txt))
    else:  # rapidocr
        arr = np.array(img)
        result, _ = engine(arr)
        for box, text, _score in (result or []):
            y_center_px = sum(pt[1] for pt in box) / len(box)
            out.append((y0 + y_center_px / zoom, text))
    out.sort(key=lambda t: t[0])
    return out


# ==========================================================================
# canonical line mapping (results-table row label -> FinancialModel line)
# ==========================================================================
_LINE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("Value of Sales & Services", (r"value of sales",)),
    ("GST Recovered",             (r"gst recovered", r"less.*gst")),
    ("Sales",                     (r"revenue from operations", r"^revenue from")),
    ("Other Income",              (r"o\w{0,2}her income",)),
    ("Total Income",              (r"^total income",)),
    ("Cost of Materials Consumed",(r"cost of material",)),
    ("Purchases of Stock-in-Trade",(r"purchases? of stock",)),
    ("Changes in Inventories",    (r"changes? in inventor",)),
    ("Excise Duty",               (r"excise du",)),
    ("Employee Benefits Expense", (r"employee benefit",)),
    ("Finance Costs",             (r"finance cost",)),
    ("Depreciation",              (r"depreciation",)),
    ("Other Expenses",            (r"other expense",)),
    ("Total Expenses",            (r"total expense",)),
    ("Earnings Before Tax",       (r"profit before tax", r"profit/.*before tax")),
    ("Current Tax",               (r"current\s*tax",)),
    ("Deferred Tax",              (r"deferred\s*tax",)),
    ("Net Profit",                (r"profit after tax and share", r"profit for the period")),
    ("Share of Profit of Associates", (r"share of profit.{0,40}associat",)),
    ("Profit After Tax",          (r"pro[a-z]{0,3}t after tax\b(?!.*share)",)),
    ("Total Comprehensive Income",(r"total comprehensive income for",)),
]


def _match_line(label: str) -> str | None:
    n = _norm(label).lower()
    if not n:
        return None
    for canon, pats in _LINE_PATTERNS:
        for p in pats:
            if re.search(p, n):
                return canon
    return None


# ==========================================================================
# period detection
# ==========================================================================
@dataclass
class _Period:
    label: str
    end: date
    kind: str                  # "quarter" | "annual"
    x0: float
    x1: float
    center: float = 0.0
    source: str = "text"       # "text" | "image"


def _detect_periods(page) -> list[_Period]:
    """Find the data columns and label/classify each reporting period."""
    words = page.extract_words()
    if not words:
        return []

    date_words = []
    for w in words:
        cleaned = _clean_ocr_date(w["text"])
        m = _DATE_RE.search(cleaned)
        if m and w["top"] < page.height * 0.35:
            d = _parse_date(m.group(1), m.group(2), m.group(3))
            if d:
                date_words.append((w, d))

    annual_x = None
    for w in words:
        t = w["text"].lower()
        if (t.startswith("year") or "annual" in t) and w["top"] < page.height * 0.35:
            annual_x = w["x0"] if annual_x is None else min(annual_x, w["x0"])

    image_cols: list[tuple[float, float]] = []
    for im in getattr(page, "images", []):
        w = im["x1"] - im["x0"]
        h = im["bottom"] - im["top"]
        if h > page.height * 0.4 and w < page.width * 0.30 and im["x0"] > page.width * 0.30:
            image_cols.append((im["x0"], im["x1"]))
    image_cols.sort()

    periods: list[_Period] = []
    for w, d in date_words:
        cx = (w["x0"] + w["x1"]) / 2.0
        src, col_x0, col_x1 = "text", w["x0"] - 30, w["x1"] + 30
        for ix0, ix1 in image_cols:
            if ix0 - 6 <= cx <= ix1 + 6:
                src, col_x0, col_x1, cx = "image", ix0, ix1, (ix0 + ix1) / 2.0
                break
        kind = "annual" if (annual_x is not None and cx >= annual_x - 6) else "quarter"
        periods.append(_Period(_period_label(d), d, kind, col_x0, col_x1, cx, src))

    known = [p.center for p in periods]
    for ix0, ix1 in image_cols:
        c = (ix0 + ix1) / 2.0
        if all(abs(c - kc) > 25 for kc in known):
            kind = "annual" if (annual_x is not None and c >= annual_x - 6) else "quarter"
            if kind == "annual":                     # only keep an undated image col if annual
                periods.append(_Period("FY?", date(1900, 1, 1), "annual", ix0, ix1, c, "image"))

    periods.sort(key=lambda p: p.center)
    deduped: list[_Period] = []
    for p in periods:
        if deduped and abs(p.center - deduped[-1].center) < 20:
            continue
        deduped.append(p)
    return deduped


# ==========================================================================
# results-table extraction (page with the consolidated P&L)
# ==========================================================================
def _center(w) -> float:
    return (w["top"] + w["bottom"]) / 2.0


def _center_x(w) -> float:
    return (w["x0"] + w["x1"]) / 2.0


@dataclass
class _Line:
    y: float
    label: str
    text_vals: dict[str, float]
    has_value: bool


def _cluster_lines(page, periods: list[_Period], decimals: bool = False) -> list[_Line]:
    """Group words into physical lines by vertical centre; split each into a
    label (left of the data columns) and the nearest-column numeric value.

    ``decimals`` selects integer money parsing (P&L page) vs decimal parsing
    (ratio table page)."""
    first_col_x = min(p.x0 for p in periods)
    text_cols = [p for p in periods if p.source == "text"]
    words = sorted(page.extract_words(), key=_center)

    clusters: list[list[dict]] = []
    cur: list[dict] = []
    cur_y = None
    for w in words:
        c = _center(w)
        if cur_y is None or abs(c - cur_y) <= 4.0:
            cur.append(w)
            cur_y = c if cur_y is None else (cur_y * (len(cur) - 1) + c) / len(cur)
        else:
            clusters.append(cur)
            cur, cur_y = [w], c
    if cur:
        clusters.append(cur)

    lines: list[_Line] = []
    for cl in clusters:
        label_words = [w for w in cl if _center_x(w) < first_col_x - 2]
        label = _norm(" ".join(w["text"] for w in sorted(label_words, key=lambda w: w["x0"])))
        buckets: dict[str, list[dict]] = {p.label: [] for p in text_cols}
        for w in cl:
            if not re.search(r"\d", w["text"]):
                continue
            cx = _center_x(w)
            if cx < first_col_x - 2:
                continue
            nearest = min(text_cols, key=lambda p: abs(cx - p.center), default=None)
            if nearest and abs(cx - nearest.center) <= 40:
                buckets[nearest.label].append(w)
        text_vals: dict[str, float] = {}
        for plabel, ws in buckets.items():
            if not ws:
                continue
            joined = "".join(w["text"] for w in sorted(ws, key=lambda w: w["x0"]))
            v = _num_dec(joined) if decimals else _num_int(joined)
            if v is not None:
                text_vals[plabel] = v
        y = sum(_center(w) for w in cl) / len(cl)
        lines.append(_Line(y, label, text_vals, bool(text_vals)))
    return lines


def _nearest_ocr(tokens: list[tuple[float, str]], y: float,
                 decimals: bool) -> float | None:
    best, best_dy = None, 6.0
    for ty, text in tokens:
        dy = abs(ty - y)
        if dy < best_dy:
            best, best_dy = text, dy
    if best is None:
        return None
    return _num_dec(best) if decimals else _num_int(best)


def _extract_results_table(page, periods: list[_Period], page_index: int
                           ) -> tuple[dict, dict, list[str]]:
    """canonical line -> {period: value} for the consolidated P&L page, plus
    per-value provenance source strings and any OCR warnings."""
    if not periods:
        return {}, {}, ["no reporting periods detected on page"]

    lines = _cluster_lines(page, periods)
    image_cols = [p for p in periods if p.source == "image"]
    value_ys = [ln.y for ln in lines if ln.has_value]
    y0 = (min(value_ys) - 12) if value_ys else 150.0
    y1 = (max(value_ys) + 12) if value_ys else page.height

    warnings: list[str] = []
    ocr_cache: dict[str, list[tuple[float, str]]] = {}
    for p in image_cols:
        toks = _ocr_column(page, p.x0, p.x1, max(0, y0), min(page.height, y1))
        ocr_cache[p.label] = toks
        if not toks:
            warnings.append(
                f"{p.label}: this column is rasterised but OCR was unavailable "
                "or empty — its cells are left unavailable rather than guessed.")

    values: dict[str, dict[str, float]] = {}
    provenance: dict[str, dict[str, str]] = {}
    pending: list[str] = []
    for ln in lines:
        if not ln.has_value:
            if ln.label:
                pending.append(ln.label)
            continue
        full_label = _norm(" ".join([*pending, ln.label]))
        pending = []
        canon = _match_line(full_label)
        if not canon:
            continue
        for plabel, v in ln.text_vals.items():
            values.setdefault(canon, {})[plabel] = v
            provenance.setdefault(canon, {})[plabel] = "pdf_raw:text"
        for p in image_cols:
            v = _nearest_ocr(ocr_cache.get(p.label, []), ln.y, decimals=False)
            if v is None:
                continue
            values.setdefault(canon, {})[p.label] = v
            provenance.setdefault(canon, {})[p.label] = "pdf_raw:ocr"
    return values, provenance, warnings


# --------------------------------------------------------------------------
# accounting-identity validation + single-outlier reconciliation
# --------------------------------------------------------------------------
_EXPENSE_COMPONENTS = (
    "Cost of Materials Consumed", "Purchases of Stock-in-Trade",
    "Changes in Inventories", "Excise Duty", "Employee Benefits Expense",
    "Finance Costs", "Depreciation", "Other Expenses",
)


def _reconcile_expenses(values, provenance, periods) -> list[str]:
    warns: list[str] = []
    tol = 3.0
    labels = [p.label for p in periods]
    for lbl in labels:
        total = values.get("Total Expenses", {}).get(lbl)
        if total is None:
            continue
        present = {c: values[c][lbl] for c in _EXPENSE_COMPONENTS
                   if c in values and lbl in values[c]}
        if len(present) < len(_EXPENSE_COMPONENTS):
            continue
        residual = total - sum(present.values())
        if abs(residual) <= tol:
            continue
        other = next((l for l in labels if l != lbl), None)
        suspect, worst = None, 0.0
        for c, v in present.items():
            ov = values.get(c, {}).get(other) if other else None
            if not ov:
                continue
            dev = abs(v - ov) / abs(ov)
            if dev > 0.6 and dev > worst:
                suspect, worst = c, dev
        if suspect is not None:
            values[suspect].pop(lbl, None)
            provenance.get(suspect, {}).pop(lbl, None)
            warns.append(
                f"{lbl}: '{suspect}' failed the expense-sum check and was an "
                f"outlier vs {other}; left unavailable rather than shown wrong.")
        else:
            warns.append(
                f"{lbl}: expense components do not reconcile to Total Expenses "
                f"(off by {residual:,.0f}); values kept but flagged.")
    return warns


def _validate_identities(values, periods) -> dict[str, bool]:
    ok: dict[str, bool] = {}
    tol = 2.0
    for p in periods:
        lbl = p.label

        def g(name):
            return values.get(name, {}).get(lbl)

        checks = []
        gross, gst, sales = g("Value of Sales & Services"), g("GST Recovered"), g("Sales")
        if None not in (gross, gst, sales):
            checks.append(abs(gross - gst - sales) <= tol)
        rev, oi, ti = g("Sales"), g("Other Income"), g("Total Income")
        if None not in (rev, oi, ti):
            checks.append(abs(rev + oi - ti) <= tol)
        ti2, te, pbt = g("Total Income"), g("Total Expenses"), g("Earnings Before Tax")
        if None not in (ti2, te, pbt):
            checks.append(abs(ti2 - te - pbt) <= tol)
        pbt2, ct, dt, pat = (g("Earnings Before Tax"), g("Current Tax"),
                             g("Deferred Tax"), g("Profit After Tax"))
        if None not in (pbt2, ct, dt, pat):
            checks.append(abs(pbt2 - ct - dt - pat) <= tol)
        ok[lbl] = len(checks) >= 2 and all(checks)
    return ok


# ==========================================================================
# page-10 reported ratio table (validation reference / value-of-last-resort)
# ==========================================================================
# Label on the ratio table -> canonical name we use downstream.
_RATIO_PATTERNS: list[tuple[str, str]] = [
    ("Debt to Equity Ratio",    r"debt\s*equity ratio"),
    ("Current Ratio",           r"current ratio"),
    ("Debt to Asset Ratio",     r"total debts? to total assets"),
    ("Interest Coverage Ratio", r"interest service coverage"),
    ("Debtor Turnover Ratio",   r"debtors? turnover"),
    ("Inventory Turnover",      r"inventory turnover"),
    ("Operating Margin (reported)", r"operating margin"),
    ("Net Profit Margin (reported)", r"net\s*[,.]?\s*n?\s*%|net profit\s*%"),
]


def _match_ratio(label: str) -> str | None:
    n = _norm(label).lower()
    for canon, pat in _RATIO_PATTERNS:
        if re.search(pat, n):
            return canon
    return None


def _extract_reported_ratios(page, previous: "_Period", current: "_Period"
                             ) -> tuple[dict, dict]:
    """Read the filing's printed ratio table -> {canon: {period: value}} plus
    provenance ('text'/'ocr').

    Only the two quarter columns are read, reusing the *same* x-geometry the
    results page established, so the audited FY/annual column (and the year-ago
    quarter) can never contaminate a quarterly reported ratio. The previous
    quarter comes from the text layer; the current quarter is OCR'd from its
    rasterised strip. These validate the Python-derived ratios, or stand in as
    the value only where Python has no inputs.
    """
    words = sorted(page.extract_words(), key=_center)
    if not words:
        return {}, {}

    boundary = min(previous.center, current.center) - 55.0

    # cluster into physical lines by vertical centre
    clusters: list[list[dict]] = []
    cur_cl: list[dict] = []
    cur_y = None
    for w in words:
        c = _center(w)
        if cur_y is None or abs(c - cur_y) <= 4.0:
            cur_cl.append(w)
            cur_y = c if cur_y is None else (cur_y * (len(cur_cl) - 1) + c) / len(cur_cl)
        else:
            clusters.append(cur_cl)
            cur_cl, cur_y = [w], c
    if cur_cl:
        clusters.append(cur_cl)

    # OCR the current-quarter (rasterised) ratio strip once
    ys = [sum(_center(w) for w in cl) / len(cl) for cl in clusters]
    y0 = (min(ys) - 6) if ys else 150.0
    y1 = (max(ys) + 6) if ys else page.height
    cur_ocr = _ocr_column(page, current.x0, current.x1,
                          max(0, y0), min(page.height, y1))

    values: dict[str, dict[str, float]] = {}
    provenance: dict[str, dict[str, str]] = {}
    pending: list[str] = []
    for cl in clusters:
        y = sum(_center(w) for w in cl) / len(cl)
        label = _norm(" ".join(w["text"] for w in sorted(cl, key=lambda w: w["x0"])
                               if _center_x(w) < boundary))
        # previous quarter from text: numbers nearest the previous column centre
        prev_words = [w for w in cl if re.search(r"\d", w["text"])
                      and abs(_center_x(w) - previous.center) <= 26]
        prev_val = None
        if prev_words:
            prev_val = _num_dec("".join(w["text"] for w in
                                        sorted(prev_words, key=lambda w: w["x0"])))
        if prev_val is None and not label:
            continue
        canon = _match_ratio(_norm(" ".join([*pending, label])))
        if not canon:
            if label and prev_val is None:
                pending.append(label)
            continue
        pending = []
        if prev_val is not None:
            values.setdefault(canon, {})[previous.label] = prev_val
            provenance.setdefault(canon, {})[previous.label] = "text"
        cur_val = _nearest_ocr(cur_ocr, y, decimals=True)
        if cur_val is not None and 0 < abs(cur_val) < 1e5:
            values.setdefault(canon, {})[current.label] = cur_val
            provenance.setdefault(canon, {})[current.label] = "ocr"
    return values, provenance


# ==========================================================================
# Screener fallback guard (annual/TTM is never a quarterly value)
# ==========================================================================
def screener_value_is_compatible(fundamental_period: str | None,
                                 target_period: str) -> tuple[bool, str]:
    """Decide whether a Screener value may stand in for a quarterly metric.

    Screener exposes annual + TTM fundamentals only, so this returns False for
    anything annual/TTM/trailing/FY/year — which is why, in practice, a metric
    the PDF cannot supply ends up 'unavailable' rather than borrowing an
    incompatible number. Pure function so the guard is unit-testable.
    """
    if not fundamental_period:
        return False, "Screener returned no period information"
    low = fundamental_period.lower()
    for bad in ("annual", "ttm", "trailing", "fy", "year"):
        if bad in low:
            return False, ("Screener value is "
                           + repr(fundamental_period)
                           + " (annual/TTM) - not comparable to quarter "
                           + target_period)
    return False, ("Screener period " + repr(fundamental_period)
                   + " is not a recognised quarterly match for " + target_period)


def _screener_fallback(metric: str, period: str, company: str,
                       lookup) -> "Provenance":
    """Attempt a Screener value for a metric the PDF cannot supply, then apply
    the compatibility guard. Always returns a Provenance (usually unavailable)."""
    if lookup is None:
        return Provenance(None, period, SOURCE_UNAVAILABLE,
                          note="Screener not consulted (no lookup configured).")
    try:
        sc = lookup(company)
    except Exception as exc:                         # noqa: BLE001
        return Provenance(None, period, SOURCE_UNAVAILABLE,
                          note="Screener lookup failed: " + str(exc))
    if sc is None:
        return Provenance(None, period, SOURCE_UNAVAILABLE,
                          note="Screener had no data for this company.")
    fp = getattr(sc, "fundamental_period", None)
    ok, why = screener_value_is_compatible(fp, period)
    if not ok:
        return Provenance(None, period, SOURCE_UNAVAILABLE,
                          note="Screener fallback rejected: " + why)
    return Provenance(None, period, SOURCE_UNAVAILABLE,
                      note="Screener value passed the period guard but no "
                           "quarterly metric mapping is defined; unavailable.")


# ==========================================================================
# Python-first derivation (the arithmetic is always Python, never the LLM)
# ==========================================================================
# Metrics whose provenance value is a percentage (stored x100); model.ratios
# keeps the fraction so the existing sector bands still apply.
_PERCENT_METRICS = {
    "Sales Growth", "Net Profit Growth", "EBITDA Margin", "EBIT Margin",
    "Net Profit Margin", "Tax Payout %", "Interest % Sales", "Depreciation % Sales",
}
# Which PDF-reported ratio validates which Python metric (same definition only).
_VALIDATION_MAP = {"Interest Coverage Ratio": "Interest Coverage Ratio"}
# PDF-reported balance-sheet ratios Python cannot compute from a P&L alone.
_REPORTED_ONLY = ("Debt to Equity Ratio", "Current Ratio", "Debt to Asset Ratio",
                  "Debtor Turnover Ratio", "Inventory Turnover")


def _safe_div(n, d):
    if n is None or d in (None, 0):
        return None
    return n / d


def derive_quarterly(raw, reported, quarters, company, screener_lookup=None):
    """
    Compute every quarterly ratio from the raw PDF values, in Python.

    Returns (ratios DataFrame [canonical x period], provenance map
    {metric: {period: Provenance.as_dict()}}). Margins & growth are fractions
    (to match the sector bands); other ratios are as-is.

    Order of resort per metric: python_derived -> pdf_reported_validation ->
    screener_fallback (guarded) -> unavailable. A PDF-reported ratio validates
    the Python figure when Python can compute it, and only becomes the value
    when Python cannot.
    """
    cols = [p.label for p in quarters]
    prov: dict = {}
    frame_vals: dict = {}

    def raw_at(name, per):
        return raw.get(name, {}).get(per)

    def put(metric, per, value_fraction, source, formula,
            validation="not_available", note=""):
        is_pct = metric in _PERCENT_METRICS
        pv = None if value_fraction is None else (
            value_fraction * 100 if is_pct else value_fraction)
        unit = "%" if is_pct else (
            "x" if ("Ratio" in metric or "Turnover" in metric
                    or "Coverage" in metric) else "")
        prov.setdefault(metric, {})[per] = Provenance(
            pv, per, source, formula, validation, unit, note).as_dict()
        if value_fraction is not None and source in (SOURCE_PY, SOURCE_PDF_REPORTED):
            frame_vals.setdefault(metric, {})[per] = value_fraction

    # ---- Python-derived level metrics, per quarter ----
    for per in cols:
        sales = raw_at("Sales", per)
        pbt = raw_at("Earnings Before Tax", per)
        fin = raw_at("Finance Costs", per)
        dep = raw_at("Depreciation", per)
        net = raw_at("Net Profit", per)
        ctax, dtax = raw_at("Current Tax", per), raw_at("Deferred Tax", per)

        ebitda = None if pbt is None else pbt + (fin or 0) + (dep or 0)
        ebit = None if pbt is None else pbt + (fin or 0)
        tax = None
        if ctax is not None or dtax is not None:
            tax = (ctax or 0) + (dtax or 0)

        rows = [
            ("EBITDA Margin", _safe_div(ebitda, sales),
             "(PBT + Finance Costs + Depreciation) / Revenue from Operations"),
            ("EBIT Margin", _safe_div(ebit, sales),
             "(PBT + Finance Costs) / Revenue from Operations"),
            ("Net Profit Margin", _safe_div(net, sales),
             "Net Profit / Revenue from Operations"),
            ("Interest % Sales", _safe_div(fin, sales), "Finance Costs / Revenue"),
            ("Depreciation % Sales", _safe_div(dep, sales), "Depreciation / Revenue"),
            ("Tax Payout %", _safe_div(tax, pbt), "(Current Tax + Deferred Tax) / PBT"),
            ("Interest Coverage Ratio", _safe_div(ebit, fin),
             "(PBT + Finance Costs) / Finance Costs"),
        ]
        for metric, val, formula in rows:
            if val is None:
                continue
            validation, note = "not_available", ""
            rep_key = _VALIDATION_MAP.get(metric)
            if rep_key and reported.get(rep_key, {}).get(per) is not None:
                rep = reported[rep_key][per]
                if abs(rep) > 1e-9 and abs(val - rep) / abs(rep) <= 0.02:
                    validation = "pass"
                else:
                    validation = "mismatch"
                    note = ("PDF-reported " + rep_key + " = " + str(rep)
                            + "; Python = " + str(round(val, 4)))
            put(metric, per, val, SOURCE_PY, formula, validation, note)

    # ---- QoQ growth (current vs previous) ----
    if len(cols) == 2:
        prev, cur = cols[0], cols[1]
        for metric, line in (("Sales Growth", "Sales"),
                             ("Net Profit Growth", "Net Profit")):
            a, b = raw_at(line, prev), raw_at(line, cur)
            formula = "(" + line + "_current / " + line + "_previous - 1) x 100"
            if a not in (None, 0) and b is not None:
                put(metric, cur, b / a - 1.0, SOURCE_PY, formula)
            else:
                put(metric, cur, None, SOURCE_UNAVAILABLE, formula,
                    note="a required period value is unavailable")

    # ---- PDF-reported-only balance-sheet ratios (Python cannot compute) ----
    for metric in _REPORTED_ONLY:
        for per in cols:
            rep = reported.get(metric, {}).get(per)
            if rep is not None:
                put(metric, per, rep, SOURCE_PDF_REPORTED,
                    "reported in the filing's ratio table; not independently "
                    "computable from a P&L (needs the balance sheet)",
                    validation="not_available",
                    note="value taken from the PDF, not Python-derived")
            else:
                prov.setdefault(metric, {})[per] = _screener_fallback(
                    metric, per, company, screener_lookup).as_dict()

    # ---- metrics with no PDF input at all (need balance sheet / cash flow) ----
    for metric in ("Return on Equity (ROE) %", "Return on Capital Employed (ROCE) %",
                   "Return on Assets (ROA) %", "Cash Conversion Cycle",
                   "CFO / PAT", "Fixed Asset Turnover"):
        for per in cols:
            prov.setdefault(metric, {})[per] = _screener_fallback(
                metric, per, company, screener_lookup).as_dict()

    frame = (pd.DataFrame(frame_vals).T.reindex(columns=cols)
             if frame_vals else pd.DataFrame(columns=cols))
    return frame, prov


# ==========================================================================
# model assembly + orchestration
# ==========================================================================
def _require_libs() -> None:
    missing = []
    if not _HAVE_PDFPLUMBER:
        missing.append("pdfplumber")
    if not _HAVE_PYMUPDF:
        missing.append("PyMuPDF")
    if missing:
        raise QuarterlyPDFError(
            "Quarterly-PDF support needs " + " and ".join(missing)
            + ". Install with:  pip install " + " ".join(m.lower() for m in missing))


def _read_bytes(source):
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if hasattr(source, "read"):
        try:
            source.seek(0)
        except Exception:                            # noqa: BLE001
            pass
        return source.read()
    with open(source, "rb") as fh:
        return fh.read()


def _find_results_page(pdf):
    """Consolidated quarterly *results table* - not the auditor-report prose,
    not the standalone section."""
    title = re.compile(
        r"un\s*-?\s*audited\s+consolidated\s+financial\s+results\s+for\s+the\s+quarter",
        re.IGNORECASE)
    standalone = re.compile(r"standalone\s+financial\s+results", re.IGNORECASE)
    for i, page in enumerate(pdf.pages):
        norm = _norm(page.extract_text() or "")
        if not title.search(norm):
            continue
        if standalone.search(norm) and not title.search(norm):
            continue
        if "particulars" in norm.lower() and _DATE_RE.search(_clean_ocr_date(norm)):
            return i
    return None


def _company_name(pdf, page_index):
    page = pdf.pages[page_index]
    for line in (page.extract_text() or "").splitlines():
        s = _norm(line)
        if not s or re.search(r"unaudited\s+consolidated", s, re.IGNORECASE):
            continue
        low = s.lower()
        if any(k in low for k in ("limited", "ltd", "industries", "corporation")):
            return s
    first = pdf.pages[0].extract_text() or ""
    m = re.search(r"([A-Z][A-Za-z&.,'\- ]+(?:Limited|Ltd))", first)
    return _norm(m.group(1)) if m else "Unknown Company"


def _company_symbol(pdf):
    for i in range(min(2, len(pdf.pages))):
        text = pdf.pages[i].extract_text() or ""
        m = re.search(r"(?:Trading\s+Symbol|Symbol)\s*[:\-]?\s*([A-Z][A-Z0-9&]+)", text)
        if m:
            return m.group(1)
    return None


class _PageBridge:
    """pdfplumber for geometry, PyMuPDF for rendering pixmaps."""
    def __init__(self, plumber_page, mupdf_page):
        self._p, self._m = plumber_page, mupdf_page
        self.width = plumber_page.width
        self.height = plumber_page.height
        self.images = plumber_page.images

    def extract_words(self, *a, **k):
        return self._p.extract_words(*a, **k)

    def get_pixmap(self, *a, **k):
        return self._m.get_pixmap(*a, **k)


def load_quarterly_pdf(source, screener_lookup=None):
    """
    Parse a listed company's consolidated quarterly-results PDF into a
    two-quarter FinancialModel with full per-metric provenance.

    `source` may be a path, bytes, or a file-like object (Streamlit uploader).
    `screener_lookup` is an optional callable (company -> object with a
    ``fundamental_period`` attribute) used only for the guarded fallback; when
    omitted, metrics the PDF cannot supply stay unavailable.
    """
    _require_libs()
    raw_bytes = _read_bytes(source)

    import pdfplumber
    import pymupdf as _pm
    pdf = pdfplumber.open(io.BytesIO(raw_bytes))
    doc = _pm.open(stream=raw_bytes, filetype="pdf")
    try:
        page_index = _find_results_page(pdf)
        if page_index is None:
            raise QuarterlyPDFError(
                "No 'Unaudited Consolidated Financial Results for the Quarter "
                "Ended ...' table was found. Only consolidated quarterly-results "
                "PDFs are supported.")

        company = _company_name(pdf, page_index) or "Unknown Company"
        symbol = _company_symbol(pdf)

        results_page = _PageBridge(pdf.pages[page_index], doc[page_index])
        periods = _detect_periods(pdf.pages[page_index])
        if not periods:
            raise QuarterlyPDFError(
                "Found the consolidated results section but could not read its "
                "reporting-period headers.")

        raw_values, raw_prov, warns = _extract_results_table(
            results_page, periods, page_index)
        warns = warns + _reconcile_expenses(raw_values, raw_prov, periods)
        validations = _validate_identities(raw_values, periods)

        quarters = sorted((p for p in periods if p.kind == "quarter"),
                          key=lambda p: p.end)
        if len(quarters) < 2:
            raise QuarterlyPDFError(
                "Could not identify two quarterly periods in the consolidated "
                "results table (found " + str(len(quarters)) + ").")
        previous, current = quarters[-2], quarters[-1]
        cols = [previous.label, current.label]

        # Reported-ratio table (usually the next page, sometimes the same page).
        # Reuses the two quarter columns' geometry so the FY/annual column can
        # never contaminate a quarterly reported ratio. Best-effort.
        reported = {}
        for cand in (page_index + 1, page_index):
            if 0 <= cand < len(pdf.pages):
                rp = _PageBridge(pdf.pages[cand], doc[cand])
                rep, _repprov = _extract_reported_ratios(rp, previous, current)
                if rep:
                    reported = rep
                    break

        # historical (raw P&L lines, two quarters)
        records = {}
        for canon, per in raw_values.items():
            rowvals = [per.get(previous.label), per.get(current.label)]
            if all(v is None for v in rowvals):
                continue
            records[canon] = rowvals
        hist = pd.DataFrame.from_dict(records, orient="index", columns=cols)
        if "Finance Costs" in hist.index and "Interest" not in hist.index:
            hist.loc["Interest"] = hist.loc["Finance Costs"]

        # Python-first derivation + provenance
        ratios, metrics_prov = derive_quarterly(
            raw_values, reported, [previous, current], company, screener_lookup)

        # raw-line provenance (for the two quarters), as plain dicts
        raw_provenance = {}
        for canon in records:
            for per in cols:
                v = raw_values.get(canon, {}).get(per)
                if v is None:
                    continue
                src = raw_prov.get(canon, {}).get(per, "pdf_raw")
                raw_provenance.setdefault(canon, {})[per] = Provenance(
                    v, per, SOURCE_PDF_RAW, formula="", unit="INR crore",
                    validation="pass" if validations.get(per) else "not_available",
                    note=src).as_dict()

        model = FinancialModel()
        model.company = company
        model.historical = hist
        model.ratios = ratios
        model.years = cols
        model.sections = {n: "QUARTERLY P&L" for n in hist.index}
        model.sections.update({n: "QUARTERLY RATIO" for n in ratios.index})
        model.meta = {
            "company": company,
            "source_company": company,
            "source_symbol": symbol,
            "periodicity": "quarterly",
            "current_period": current.label,
            "previous_period": previous.label,
            "current_quarter_tag": _fy_quarter_tag(current.end),
            "previous_quarter_tag": _fy_quarter_tag(previous.end),
            "current_period_end": current.end.isoformat(),
            "previous_period_end": previous.end.isoformat(),
            "source_type": "quarterly_pdf",
            "source_scope": "consolidated",
            "source_pages": [page_index + 1, page_index + 2],
            "quarter_column_centers": [previous.center, current.center],
            "column_source": {current.label: current.source,
                              previous.label: previous.source},
            "validated": {current.label: validations.get(current.label, False),
                          previous.label: validations.get(previous.label, False)},
            "warnings": warns,
            "reporting_currency": "INR crore",
            "raw_provenance": raw_provenance,
            "metrics": metrics_prov,
            "reported_ratios": reported,
        }
        return model
    finally:
        pdf.close()
        doc.close()


# Backwards-compatible alias (an earlier prototype used this name).
load_quarterly_model = load_quarterly_pdf


def looks_like_quarterly_pdf(source, *, sniff_pages=12):
    """Cheap text-only check used to auto-route an upload. Never raises."""
    if not _HAVE_PDFPLUMBER:
        return False
    try:
        import pdfplumber
        raw = _read_bytes(source)
        pdf = pdfplumber.open(io.BytesIO(raw))
    except Exception:                                # noqa: BLE001
        return False
    try:
        title = re.compile(
            r"consolidated\s+financial\s+results\s+for\s+the\s+quarter",
            re.IGNORECASE)
        for i, page in enumerate(pdf.pages):
            if i >= sniff_pages:
                break
            if title.search(_norm(page.extract_text() or "")):
                return True
        return False
    except Exception:                                # noqa: BLE001
        return False
    finally:
        try:
            pdf.close()
        except Exception:                            # noqa: BLE001
            pass


def is_pdf(name, data=None):
    """True if the upload is a PDF, by extension or magic bytes."""
    if name and name.lower().endswith(".pdf"):
        return True
    if data and data[:5] == b"%PDF-":
        return True
    return False
