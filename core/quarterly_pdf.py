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

from . import quarterly_semantics as qs
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


# Dates, period labels and number parsing now live in the document-agnostic
# semantics module; these thin wrappers keep call sites stable.
_period_label = qs.period_label
_fy_quarter_tag = qs.fy_quarter_tag


def _num_int(text: Any) -> float | None:
    """Integer money cell (crore filings) — parse with no implied decimals."""
    return qs.parse_amount(text, 0)


def _num_dec(text: Any) -> float | None:
    """Decimal cell (a reported ratio / EPS) — the '.' is always a real decimal."""
    return qs.parse_ratio(text)


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


# Row-label -> canonical FinancialModel line now lives in the semantics module.
_match_line = qs.match_row_label


# ==========================================================================
# period detection
# ==========================================================================
@dataclass
class _Period:
    label: str
    end: date
    kind: str                  # "quarter" | "annual" | "ttm" | "cumulative"
    x0: float
    x1: float
    center: float = 0.0
    source: str = "text"       # "text" | "image"


def _header_dates(words, page_height: float) -> list[tuple[float, float, "date"]]:
    """Find reporting-period dates in the header band, combining adjacent words.

    Date headers are often split across words ("June" + "30,2026") or glued
    ("Jun'26"), and differ company-to-company, so we cluster header words into
    lines and slide a 1-3 word window, parsing each candidate. Returns
    [(center_x, top, date)], de-duplicated by x.
    """
    band = sorted((w for w in words if w["top"] < page_height * 0.35),
                  key=lambda w: w["top"])
    # agglomerative line grouping (tolerant), so a row whose word tops differ by
    # a pixel or two is not split across the /4 bin boundary.
    line_groups: list[list[dict]] = []
    cur_top = None
    for w in band:
        if cur_top is None or abs(w["top"] - cur_top) <= 3.5:
            if not line_groups:
                line_groups.append([])
            line_groups[-1].append(w)
            cur_top = w["top"] if cur_top is None else (cur_top + w["top"]) / 2
        else:
            line_groups.append([w])
            cur_top = w["top"]

    per_line: list[tuple[float, list[tuple[float, float, object]]]] = []
    for row in line_groups:
        key = row[0]["top"]
        row.sort(key=lambda w: w["x0"])
        hits: list[tuple[float, float, object]] = []
        i = 0
        while i < len(row):
            matched = None
            # shortest window first: a glued "Jun'26" (k=1) or a split
            # "June 30,2026" (k=2) should NOT swallow the next column's word.
            for k in (1, 2, 3):
                grp = row[i:i + k]
                if len(grp) < k or grp[-1]["x1"] - grp[0]["x0"] > 90:
                    continue
                d = qs.parse_date_token(" ".join(w["text"] for w in grp))
                if d:
                    matched = (k, (grp[0]["x0"] + grp[-1]["x1"]) / 2.0,
                               grp[0]["top"], d)
                    break
            if matched:
                hits.append((matched[1], matched[2], matched[3]))
                i += matched[0]
            else:
                i += 1
        if hits:
            per_line.append((key, hits))
    if not per_line:
        return []
    # The real period-header row is the line with the most dates; ties broken by
    # the lower line (nearer the data). This ignores the title's stray date.
    per_line.sort(key=lambda kh: (len(kh[1]), kh[0]), reverse=True)
    best = sorted(per_line[0][1], key=lambda t: t[0])
    deduped: list[tuple[float, float, object]] = []
    for cx, top, d in best:
        if deduped and abs(cx - deduped[-1][0]) < 20:
            continue
        deduped.append((cx, top, d))
    return deduped


def _group_header_type(words, cx: float, band_bottom: float) -> str:
    """Classify a column's period type from the group-header text sitting above
    it ("Quarter ended" / "Year ended" / "Nine months ended" / "TTM"). Returns
    a qs period-type or ''."""
    near = [w for w in words
            if w["top"] < band_bottom and abs(_center_x(w) - cx) < 70]
    text = " ".join(w["text"] for w in sorted(near, key=lambda w: (w["top"], w["x0"])))
    return qs.classify_period_type(text)


def _detect_periods(page) -> list[_Period]:
    """Find the data columns and label + classify each reporting period.

    Period *type* (quarter / annual / ttm / cumulative) is inferred from the
    group header above each column, with the 'Year ended' x-region as a
    geometric backstop, so annual / TTM / nine-month columns can be excluded
    rather than mistaken for a quarter.
    """
    words = page.extract_words()
    if not words:
        return []
    band_bottom = page.height * 0.30

    # date headers, combining split/glued words into one date per column
    date_hits = _header_dates(words, page.height)

    annual_x = None
    for w in words:
        t = w["text"].lower()
        if (t.startswith("year") or "annual" in t) and w["top"] < page.height * 0.35:
            annual_x = w["x0"] if annual_x is None else min(annual_x, w["x0"])

    image_cols: list[tuple[float, float]] = []
    for im in getattr(page, "images", []):
        iw = im["x1"] - im["x0"]
        ih = im["bottom"] - im["top"]
        if ih > page.height * 0.4 and iw < page.width * 0.30 and im["x0"] > page.width * 0.30:
            image_cols.append((im["x0"], im["x1"]))
    image_cols.sort()

    periods: list[_Period] = []
    for cx, _top, d in date_hits:
        src, col_x0, col_x1 = "text", cx - 35, cx + 35
        for ix0, ix1 in image_cols:
            if ix0 - 6 <= cx <= ix1 + 6:
                src, col_x0, col_x1, cx = "image", ix0, ix1, (ix0 + ix1) / 2.0
                break
        kind = _group_header_type(words, cx, band_bottom)
        if not kind:
            kind = "annual" if (annual_x is not None and cx >= annual_x - 6) else "quarter"
        periods.append(_Period(_period_label(d), d, kind, col_x0, col_x1, cx, src))

    known = [p.center for p in periods]
    for ix0, ix1 in image_cols:
        c = (ix0 + ix1) / 2.0
        if all(abs(c - kc) > 25 for kc in known):
            kind = _group_header_type(words, c, band_bottom) or (
                "annual" if (annual_x is not None and c >= annual_x - 6) else "quarter")
            if kind != "quarter":                     # only keep an undated non-quarter col
                periods.append(_Period("FY?", date(1900, 1, 1), kind, ix0, ix1, c, "image"))

    periods.sort(key=lambda p: p.center)
    deduped: list[_Period] = []
    for p in periods:
        if deduped and abs(p.center - deduped[-1].center) < 20:
            continue
        deduped.append(p)

    # When two columns carry the SAME period-end date, the rightmost is the
    # audited FY/annual column (the universal layout) — mark it annual so it can
    # never be mistaken for the quarter. This is OCR-independent and catches the
    # case where the 'Year ended' group header was mangled by the text layer.
    from collections import defaultdict
    by_date: dict[tuple, list[_Period]] = defaultdict(list)
    for p in deduped:
        if p.end.year > 1900:
            by_date[(p.end.year, p.end.month)].append(p)
    for group in by_date.values():
        if len(group) > 1:
            group.sort(key=lambda p: p.center)
            for extra in group[1:]:               # keep leftmost; rest -> annual
                if extra.kind == "quarter":
                    extra.kind = "annual"
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


def _cluster_lines(page, periods: list[_Period], decimals_conv: int = 0) -> list[_Line]:
    """Group words into physical lines by vertical centre; split each into a
    label (left of the data columns) and the nearest-column numeric value,
    parsed with the document's decimal convention (0 for integer-crore filings,
    2 for paise/lakh filings whose text layer may have dropped the separator)."""
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
            v = qs.parse_amount(joined, decimals_conv)
            if v is not None:
                text_vals[plabel] = v
        y = sum(_center(w) for w in cl) / len(cl)
        lines.append(_Line(y, label, text_vals, bool(text_vals)))
    return lines


def _nearest_ocr(tokens: list[tuple[float, str]], y: float,
                 decimals_conv: int = 0) -> float | None:
    best, best_dy = None, 6.0
    for ty, text in tokens:
        dy = abs(ty - y)
        if dy < best_dy:
            best, best_dy = text, dy
    if best is None:
        return None
    return qs.parse_amount(best, decimals_conv)


def _detect_table_decimals(page, periods: list[_Period]) -> int:
    """Infer the decimal convention from the numeric cells in the value columns."""
    centers = [p.center for p in periods]
    tokens = []
    for w in page.extract_words():
        if not re.search(r"\d", w["text"]):
            continue
        cx = _center_x(w)
        if any(abs(cx - c) <= 40 for c in centers):
            tokens.append(w["text"])
    return qs.detect_decimals(tokens)


def _extract_results_table(page, periods: list[_Period], page_index: int
                           ) -> tuple[dict, dict, list[str], dict]:
    """canonical line -> {period: value} for the consolidated P&L page, plus
    per-value provenance sources, OCR warnings, and detected unit info."""
    if not periods:
        return {}, {}, ["no reporting periods detected on page"], {}

    header_text = " ".join(w["text"] for w in page.extract_words()
                           if w["top"] < page.height * 0.30)
    raw_unit, mult, unit_known = qs.detect_unit(header_text)
    decimals_conv = _detect_table_decimals(page, periods)
    unit_info = {"raw_unit": raw_unit, "multiplier_to_crore": mult,
                 "unit_known": unit_known, "decimals": decimals_conv}

    lines = _cluster_lines(page, periods, decimals_conv)
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
            v = _nearest_ocr(ocr_cache.get(p.label, []), ln.y, decimals_conv)
            if v is None:
                continue
            values.setdefault(canon, {})[p.label] = v
            provenance.setdefault(canon, {})[p.label] = "pdf_raw:ocr"
    return values, provenance, warnings, unit_info


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
            prev_val = qs.parse_ratio("".join(w["text"] for w in
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
        cur_tok = min(cur_ocr, key=lambda t: abs(t[0] - y), default=None) if cur_ocr else None
        cur_val = qs.parse_ratio(cur_tok[1]) if cur_tok and abs(cur_tok[0] - y) < 6 else None
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


# ---- page classification (semantic, never page-number based) --------------
_RESULTS_TITLE = re.compile(
    r"(financial results for the (quarter|period)|statement of (unaudited |audited )?"
    r"(consolidated |standalone )?(financial results|profit and loss)|"
    r"financial results)", re.IGNORECASE)
_PNL_ROW_MARKERS = (
    "revenue from operations", "total income", "profit before tax",
    "total expenses", "total expenditure", "tax expense", "profit for the period",
    "profit after tax", "net profit", "finance cost", "depreciation",
    "interest earned", "earnings per")


def _page_signature(text: str) -> dict:
    """Cheap semantic fingerprint of one page (no geometry)."""
    t = re.sub(r"\s+", " ", (text or "")).lower()
    consolidated = bool(re.search(r"consolidated", t))
    standalone = bool(re.search(r"standalone", t))
    is_results = bool(_RESULTS_TITLE.search(t))
    pnl_hits = sum(1 for m in _PNL_ROW_MARKERS if m in t)
    has_quarter = ("quarter ended" in t or "quarter and" in t or "quarter" in t)
    return {"consolidated": consolidated, "standalone": standalone,
            "is_results": is_results, "pnl_hits": pnl_hits, "has_quarter": has_quarter}


def classify_pages(pdf) -> list[dict]:
    """Classify every page: page_type + scope + a confidence-ish score. Used to
    locate the consolidated quarterly P&L without relying on a page number."""
    out = []
    for i, page in enumerate(pdf.pages):
        sig = _page_signature(page.extract_text() or "")
        scope = ("consolidated" if sig["consolidated"] and not
                 (sig["standalone"] and not sig["consolidated"]) else
                 "standalone" if sig["standalone"] else "unknown")
        if sig["is_results"] and sig["pnl_hits"] >= 4 and sig["has_quarter"]:
            page_type = f"{scope}_pnl"
        elif sig["pnl_hits"] >= 4:
            page_type = f"{scope}_pnl"
        elif "balance sheet" in (page.extract_text() or "").lower():
            page_type = f"{scope}_balance_sheet"
        elif "cash flow" in (page.extract_text() or "").lower():
            page_type = f"{scope}_cash_flow"
        else:
            page_type = "notes_or_other"
        out.append({"index": i, "page_type": page_type, "scope": scope,
                    "score": sig["pnl_hits"] + (2 if sig["is_results"] else 0), **sig})
    return out


def _find_results_page(pdf) -> tuple[int | None, str]:
    """Locate the consolidated quarterly P&L page semantically.

    Returns (index, status) with status in {'ok', 'standalone_only',
    'not_found'}. A page qualifies only if it carries enough P&L row markers AND
    at least two quarter columns; consolidated is strongly preferred and the
    standalone section is never used for the primary analysis.
    """
    pages = classify_pages(pdf)

    def _has_two_quarters(i: int) -> bool:
        try:
            periods = _detect_periods(pdf.pages[i])
        except Exception:                            # noqa: BLE001
            return False
        return sum(1 for p in periods if p.kind == "quarter") >= 2

    consolidated = [p for p in pages if p["page_type"] == "consolidated_pnl"
                    and p["consolidated"]]
    consolidated.sort(key=lambda p: (-p["score"], p["index"]))
    for p in consolidated:
        if _has_two_quarters(p["index"]):
            return p["index"], "ok"

    standalone = [p for p in pages if p["page_type"].startswith("standalone")
                  or (p["standalone"] and not p["consolidated"] and p["pnl_hits"] >= 4)]
    for p in standalone:
        if _has_two_quarters(p["index"]):
            return None, "standalone_only"
    return None, "not_found"


_ADDRESS_RE = re.compile(
    r"regd|regi?stered office|corporate|cin[:\s]|tel\.?[:\s]|fax|e-?mail|website|"
    r"www\.|@|phone|\bplot\b|\bfloor\b|nariman|gurugram|mumbai|road|complex|sector",
    re.IGNORECASE)


def _company_name(pdf, page_index):
    """Company name: the nearest plausible name line above the results title,
    falling back to any page-1 '...Limited/Ltd' line. Layout-agnostic."""
    lines = [_norm(l) for l in (pdf.pages[page_index].extract_text() or "").splitlines()]
    lines = [l for l in lines if l]
    title_idx = next((i for i, l in enumerate(lines)
                      if _RESULTS_TITLE.search(l)), len(lines))
    for l in lines[:title_idx]:
        low = l.lower()
        if _ADDRESS_RE.search(l) or len(l) < 4:
            continue
        if re.search(r"limited|ltd|industries|bank|hotels|corporation|company|"
                     r"finance|motors|steel|power|enterprises|technologies|labs",
                     low):
            return l
    # otherwise the first non-address line on the page
    for l in lines[:title_idx]:
        if not _ADDRESS_RE.search(l) and len(l) >= 4 and re.search(r"[A-Za-z]", l):
            return l
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


def _guess_business_type(company, raw_values, pdf, page_index) -> str | None:
    """Light business-type hint for metric applicability (bank / NBFC / etc.).

    Heuristic only — used to grey out ratios that are not meaningful for the
    business (e.g. ROCE for a bank), never to change any extracted number.
    """
    name = (company or "").lower()
    page_text = (pdf.pages[page_index].extract_text() or "").lower()
    if any(k in name for k in ("bank",)) or "interest earned" in page_text \
            or "net interest income" in page_text:
        return "banking"
    if any(k in name for k in ("insurance", "life insurance", "assurance")):
        return "insurance"
    if any(k in name for k in ("finance", "financial services", "capital",
                               "housing finance", "fin ")) or "nbfc" in page_text:
        return "financial_services"
    return None


def _overall_confidence(validations, current, previous, unit_info,
                        raw_provenance) -> str:
    """Roll per-cell confidence into one HIGH/MEDIUM/LOW label for the header."""
    cur_ok = validations.get(current.label, False)
    prev_ok = validations.get(previous.label, False)
    confs = [c.get("confidence") for by in raw_provenance.values()
             for c in by.values()]
    any_low = "low" in confs
    if cur_ok and prev_ok and unit_info.get("unit_known") and not any_low:
        return "high"
    if (cur_ok or prev_ok) and not (any_low and not (cur_ok or prev_ok)):
        return "medium"
    return "low"


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
        page_index, status = _find_results_page(pdf)
        if page_index is None:
            if status == "standalone_only":
                raise QuarterlyPDFError(
                    "Consolidated quarterly results could not be identified in "
                    "this document — only standalone results were found. "
                    "FundaCheck's quarterly analysis uses consolidated results.")
            raise QuarterlyPDFError(
                "Unable to identify quarterly financial results in this document. "
                "Upload a listed company's consolidated quarterly-results PDF.")

        company = _company_name(pdf, page_index) or "Unknown Company"
        symbol = _company_symbol(pdf)

        results_page = _PageBridge(pdf.pages[page_index], doc[page_index])
        periods = _detect_periods(pdf.pages[page_index])
        if not periods:
            raise QuarterlyPDFError(
                "Found the consolidated results section but could not read its "
                "reporting-period headers.")

        # Choose the two quarters up front (current + immediately previous), then
        # extract ONLY those columns — the annual / year-ago / TTM / cumulative
        # columns are never read into the quarterly dataset.
        quarters = sorted((p for p in periods if p.kind == "quarter"),
                          key=lambda p: p.end)
        if len(quarters) < 2:
            raise QuarterlyPDFError(
                "This document does not contain a usable two-quarter comparison "
                "(found " + str(len(quarters)) + " quarterly column(s)).")
        previous, current = quarters[-2], quarters[-1]
        two = [previous, current]
        cols = [previous.label, current.label]

        raw_values, raw_prov, warns, unit_info = _extract_results_table(
            results_page, two, page_index)
        warns = warns + _reconcile_expenses(raw_values, raw_prov, two)
        validations = _validate_identities(raw_values, two)
        if not unit_info.get("unit_known"):
            warns.append("Reporting unit could not be detected from the filing; "
                         "assuming ₹ crore for display — verify absolute figures.")

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

        # historical (raw P&L lines, two quarters) — converted to ₹ crore for
        # display using the detected unit; ratios use raw values (scale-invariant).
        mult = unit_info.get("multiplier_to_crore", 1.0)
        raw_unit = unit_info.get("raw_unit", "crore")
        records = {}
        for canon, per in raw_values.items():
            rowvals = [per.get(previous.label), per.get(current.label)]
            if all(v is None for v in rowvals):
                continue
            records[canon] = [None if v is None else v * mult for v in rowvals]
        hist = pd.DataFrame.from_dict(records, orient="index", columns=cols)
        if "Finance Costs" in hist.index and "Interest" not in hist.index:
            hist.loc["Interest"] = hist.loc["Finance Costs"]

        # Python-first derivation + provenance (ratios from raw, unit-agnostic)
        ratios, metrics_prov = derive_quarterly(
            raw_values, reported, [previous, current], company, screener_lookup)

        # raw-line provenance (for the two quarters): raw value + normalised crore
        raw_provenance = {}
        for canon in records:
            for per in cols:
                v = raw_values.get(canon, {}).get(per)
                if v is None:
                    continue
                src = raw_prov.get(canon, {}).get(per, "pdf_raw")
                conf = "high" if (src.endswith("text") and validations.get(per)) else \
                       "medium" if src.endswith("ocr") and validations.get(per) else \
                       "medium" if src.endswith("text") else "low"
                p = Provenance(v * mult, per, SOURCE_PDF_RAW, formula="",
                               unit="₹ crore",
                               validation="pass" if validations.get(per) else "not_available",
                               note=src).as_dict()
                p["raw_value"] = v
                p["raw_unit"] = raw_unit
                p["confidence"] = conf
                raw_provenance.setdefault(canon, {})[per] = p

        business_type = _guess_business_type(company, raw_values, pdf, page_index)
        overall_conf = _overall_confidence(validations, current, previous,
                                           unit_info, raw_provenance)

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
            "reporting_currency": "₹ crore",
            "source_unit": raw_unit,
            "unit_multiplier_to_crore": mult,
            "unit_known": unit_info.get("unit_known", False),
            "business_type": business_type,
            "confidence": overall_conf,
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
