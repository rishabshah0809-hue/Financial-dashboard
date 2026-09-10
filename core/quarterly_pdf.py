"""Quarterly results PDF ingestion for FundaCheck.

Phase 1 target: the unaudited consolidated financial-results section and the
latest two quarter columns. The module intentionally keeps FinancialModel as
the downstream contract so existing charts/scoring can be upgraded separately.
"""
from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd
import pdfplumber

from .parser import FinancialModel, ParseError


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _period(value: Any) -> str | None:
    text = str(value).replace("’", "'")
    match = re.search(r"([A-Za-z]{3})\s*['’]?(\d{2,4})", text)
    if not match:
        return None
    year = match.group(2)
    if len(year) == 2:
        year = "20" + year
    return f"{match.group(1).title()[:3]}-{year}"


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("₹", "")
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    if text.lower() in {"", "-", "na", "n/a", "nan"}:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    return -result if negative else result


def _find_results_page(pdf: pdfplumber.PDF) -> int:
    for index, page in enumerate(pdf.pages):
        text = (page.extract_text() or "").upper()
        if "UNAUDITED CONSOLIDATED FINANCIAL RESULTS" in text and "QUARTER ENDED" in text:
            return index
    raise ParseError("Could not find the unaudited consolidated financial-results section.")


def _company_name(text: str) -> str:
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines() if x.strip()]
    for index, line in enumerate(lines):
        if "UNAUDITED CONSOLIDATED FINANCIAL RESULTS" in line.upper() and index:
            return lines[index - 1]
    return "Unknown Company"


def _header(page: pdfplumber.page.Page) -> tuple[list[str], list[float]]:
    hits = []
    for word in page.extract_words() or []:
        period = _period(word.get("text", ""))
        if period:
            hits.append((period, (float(word["x0"]) + float(word["x1"])) / 2, float(word["top"])))
    if len(hits) < 2:
        raise ParseError("Could not identify the two quarter columns.")
    top = min(item[2] for item in hits)
    row = sorted((x for x in hits if abs(x[2] - top) < 18), key=lambda x: x[1])
    labels, centers = [], []
    for period, center, _ in row:
        if period not in labels:
            labels.append(period)
            centers.append(center)
    return labels, centers


def load_quarterly_pdf(source: Any) -> FinancialModel:
    """Create a two-period FinancialModel from a quarterly results PDF.

    This first-stage loader deliberately does not guess missing numbers. The
    next stage adds targeted OCR for rasterized table cells and explicit
    Screener fallback provenance for unavailable inputs.
    """
    if hasattr(source, "read"):
        raw = source.read()
    elif isinstance(source, (bytes, bytearray)):
        raw = bytes(source)
    else:
        with open(source, "rb") as handle:
            raw = handle.read()

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        page_index = _find_results_page(pdf)
        page = pdf.pages[page_index]
        labels, centers = _header(page)
        current, previous = labels[:2]
        text = page.extract_text() or ""
        company = _company_name(text)

        # Keep the page/table discovery and period metadata in the model now.
        # Numeric extraction is intentionally conservative until the OCR stage
        # validates each cell against the rendered table.
        model = FinancialModel(
            company=company,
            years=[current, previous],
            historical=pd.DataFrame(columns=[current, previous]),
            ratios=pd.DataFrame(columns=[current, previous]),
            meta={
                "periodicity": "quarterly",
                "source_type": "quarterly_pdf",
                "source_scope": "consolidated",
                "current_period": current,
                "previous_period": previous,
                "source_pages": [page_index + 1, page_index + 2],
                "quarter_column_centers": centers[:2],
            },
        )
        return model
