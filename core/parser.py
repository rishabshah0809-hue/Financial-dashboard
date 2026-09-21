"""
parser.py
---------
Reads a "3-statement model" Excel workbook (the layout produced by Screener.in
style templates) and turns it into clean pandas DataFrames.

The workbook layout we expect on every sheet:

      col A   col B                      col C, D, E ...
      -----   ------------------------   -------------------------
              Historical Financial Data - COMPANY NAME     <- title row
              Year                       2017-03-31  2018-03-31 ...
      #       Income Statement                              <- section header
              Sales                      36532.86    35923.92 ...
              COGS                       33410.81    32775.11 ...

So: a row is a *section header* when column A holds "#", and a *metric* row
when column B holds a label and column A is empty. That single rule is enough
to parse every sheet in the workbook, which is why the parser is short.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import synonyms as SYN

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

# Sheet names we look for, and the friendly key we store them under.
# Matching is fuzzy (lower-cased, spaces stripped) so small naming differences
# between workbooks do not break the import.
SHEET_ALIASES: dict[str, tuple[str, ...]] = {
    "historical": ("historicalfs", "historicalfinancials",
                   "historicalfinancialdata", "historical", "financials",
                   "3smodel", "3statementmodel", "model"),
    "ratios": ("ratioanalysis", "ratios", "ratio", "keyratios"),
    "common_size": ("commonsizestatement", "commonsize", "commonsizeanalysis"),
    "data": ("datasheet", "data", "raw"),
}

# When a workbook has no single combined statements sheet, it usually splits the
# statements across separate tabs. These are parsed and stacked into one frame.
# Names are matched on the NORMALIZED sheet name (lower-cased, non-alphanumerics
# stripped), so "Profit & Loss", "Profit and Loss", "P&L" and "PROFIT_LOSS" all
# land on the same alias.
STATEMENT_SHEET_ALIASES: tuple[str, ...] = (
    "incomestatement", "statementofincome", "income",
    "profitloss", "profitandloss", "profitlossaccount",
    "statementofprofitandloss", "pandl", "pl", "pnl",
    "balancesheet", "statementoffinancialposition",
    "cashflow", "cashflowstatement", "cashflowstatment",
    "statementofcashflows",
)

# Short aliases must match the WHOLE normalized sheet name — "pl" as a loose
# substring would fire on unrelated tabs ("Sample Plan" → "sampleplan").
_SHORT_ALIAS_LEN = 6

# How many canonical financial lines a sheet must show before it is accepted as
# the workbook's combined statements (see _first_parsable).
MIN_STATEMENT_CONCEPTS = 4


class ParseError(Exception):
    """Raised when a workbook does not look like a 3-statement model."""


@dataclass
class FinancialModel:
    """Everything we managed to extract from one uploaded workbook."""

    company: str = "Unknown Company"
    years: list[str] = field(default_factory=list)
    historical: pd.DataFrame = field(default_factory=pd.DataFrame)
    ratios: pd.DataFrame = field(default_factory=pd.DataFrame)
    common_size: pd.DataFrame = field(default_factory=pd.DataFrame)
    meta: dict[str, Any] = field(default_factory=dict)
    # True when the statements had to be rebuilt from the raw Data Sheet
    rebuilt_from_data_sheet: bool = False
    # metric label -> the section it was found under ("PROFITABILITY & MARGINS")
    sections: dict[str, str] = field(default_factory=dict)
    # metric label -> {"kind": "source"|"derived", "formula": "..."}
    # SOURCE  = the number was read straight out of the workbook.
    # DERIVED = FundaCheck computed it (the formula says how).
    # Kept internally so the UI can explain a number's origin; FundaCheck must
    # never present a computed figure as if the workbook supplied it.
    provenance: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def latest_year(self) -> str:
        return self.years[-1] if self.years else ""

    def series(self, metric: str) -> pd.Series:
        """Return one metric across all years, from whichever sheet has it."""
        for frame in (self.ratios, self.historical, self.common_size):
            if not frame.empty and metric in frame.index:
                return frame.loc[metric].dropna()
        return pd.Series(dtype="float64")

    def latest(self, metric: str, default: float | None = None) -> float | None:
        """Most recent non-empty value of a metric."""
        s = self.series(metric)
        return float(s.iloc[-1]) if not s.empty else default

    def metrics_in_section(self, section: str) -> list[str]:
        return [m for m, sec in self.sections.items() if sec == section]

    # -- provenance ------------------------------------------------------
    def mark_source(self, label: str, where: str = "") -> None:
        self.provenance[label] = {"kind": "source", "formula": "",
                                  "where": where}

    def mark_derived(self, label: str, formula: str = "") -> None:
        self.provenance[label] = {"kind": "derived", "formula": formula,
                                  "where": "FundaCheck"}

    def provenance_of(self, label: str) -> str:
        """"source", "derived", or "unknown" for a metric label."""
        entry = self.provenance.get(label)
        return entry["kind"] if entry else "unknown"

    def provenance_formula(self, label: str) -> str:
        entry = self.provenance.get(label)
        return entry.get("formula", "") if entry else ""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _norm(text: Any) -> str:
    """Lower-case, strip everything that is not a letter or digit."""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _clean_label(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


# Summary columns some templates append to the right of the year columns.
# They are not periods, so they must never enter a time series.
AGGREGATE_COLUMNS = {
    "mean", "median", "average", "avg", "cagr", "min", "max", "sum",
    "total", "stdev", "std", "change", "growth",
}


# Financial-model templates tag each column as actual or projected: "FY23A" is
# reported, "FY27E" / "FY27P" / "FY27F" is someone's forecast. FundaCheck scores
# REPORTED history only, so projected columns are recognised (so the header row
# is still found) and then dropped from the model rather than silently scored as
# if they had happened.
_FY_COLUMN = re.compile(r"(?i)^fy\s?['’]?(\d{2,4})\s*(a|e|p|f|est|proj)?$")
_PROJECTED_SUFFIXES = ("e", "p", "f", "est", "proj")

# An Indian financial year written as a span: "FY 2022-23", "FY2022-23",
# "2022-23", "FY 22-23", "2022-2023". The year that ENDS the span names the
# financial year, so "FY 2022-23" is FY23.
# A leading or trailing marker tags the span as an estimate: "E FY 2026-27",
# "FY 2026-27E", "P FY2026-27".
_FY_SPAN = re.compile(
    r"(?i)^(e|est|p|proj|f)?\s*(?:fy\s?)?(\d{4}|\d{2})\s*[-/–]\s*(\d{4}|\d{2})"
    r"\s*(a|e|p|f|est|proj)?$")

# A quarter column: "Q1 2023-24", "Q1FY24", "Q3 FY 2024", "1Q24". Quarters are
# recognised so the header row is still found on a sheet that mixes them with
# annual columns — and then dropped, because a quarter is not a financial year.
_QUARTER_COLUMN = re.compile(
    r"(?i)^(?:q\s?([1-4])|([1-4])\s?q)\s*(?:fy\s?)?['’]?(\d{2,4})?(?:\s*[-/–]\s*(\d{2,4}))?$")


def _fy_span_label(text: str) -> str | None:
    """'FY 2022-23' -> 'FY23'. The closing year names the financial year."""
    match = _FY_SPAN.match(text.strip())
    if not match:
        return None
    start, end = match.group(2), match.group(3)
    marker = (match.group(1) or match.group(4) or "").lower()
    # "2022-23" and "2022-2023" both close in 2023; a 2-digit close rolls the
    # century of the opening year forward ("99-00" -> FY00).
    # A financial-year span closes exactly one year after it opens
    # ("2022-23", "2022-2023", "99-00"). This is what separates a span from a
    # date fragment such as "2017-03", whose "03" is a month, not a year.
    if (int(start[-2:]) + 1) % 100 != int(end[-2:]) % 100:
        return None
    label = f"FY{end[-2:]}"
    # Keep the projection marker so _is_period() can drop a forecast column.
    return f"{label}E" if marker in _PROJECTED_SUFFIXES else label


def _quarter_label(text: str) -> str | None:
    """'Q1 2023-24' -> 'Q1FY24' (a label _is_period() then rejects)."""
    match = _QUARTER_COLUMN.match(text.strip())
    if not match:
        return None
    quarter = match.group(1) or match.group(2)
    year = match.group(4) or match.group(3) or ""
    return f"Q{quarter}FY{year[-2:]}" if year else f"Q{quarter}"


def _is_quarter(label: Any) -> bool:
    return bool(_QUARTER_COLUMN.match(str(label).strip())) or bool(
        re.match(r"(?i)^q[1-4]fy\d{0,2}$", str(label).strip()))


def _is_projection(label: Any) -> bool:
    match = _FY_COLUMN.match(str(label).strip())
    return bool(match and (match.group(2) or "").lower() in _PROJECTED_SUFFIXES)


def _is_period(label: str) -> bool:
    """True for a real reporting period (FY24, TTM), False for Mean/Median/CAGR
    and for projected columns (FY27E)."""
    if not label:
        return False
    if _is_projection(label) or _is_quarter(label):
        return False
    return _norm(label) not in AGGREGATE_COLUMNS


def _looks_period(value: Any) -> bool:
    """
    True if a *cell* reads as a reporting period: a real date (2017-03-31), a
    year (2017), FY24, or TTM/LTM. Used to locate the header row of a sheet
    without relying on a literal "Year" label, so differently-laid-out workbooks
    still parse.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    if isinstance(value, pd.Timestamp) or hasattr(value, "year"):
        return True
    if isinstance(value, (int, float)):
        # A NUMBER is only a period when it is a whole, plausible year. Without
        # this a data cell such as 21047.45 reads as "2104" and a row of figures
        # is mistaken for the header row.
        return float(value).is_integer() and 1900 <= int(value) <= 2100
    text = str(value).strip()
    if _FY_COLUMN.match(text) or _QUARTER_COLUMN.match(text):
        return True
    if _fy_span_label(text):
        return True
    # A plain year or an ISO-style date: 2017, 2017-03, 2017-03-31 (with or
    # without a trailing time, which is how pandas stringifies a datetime).
    if re.match(r"^(19|20)\d{2}([-/]\d{1,2}){0,2}( \d{2}:\d{2}:\d{2})?$", text):
        return True
    return _norm(text) in ("ttm", "ltm")


def _year_label(value: Any) -> str:
    """Turn a date cell (or anything else) into a short year label like FY25."""
    if isinstance(value, pd.Timestamp) or hasattr(value, "year"):
        year = value.year
        # Indian financial years end in March, so a 2025-03-31 column is FY25.
        return f"FY{str(year)[-2:]}"
    text = str(value).strip()
    if _norm(text) in ("ttm", "ltm", "trailing"):
        return "TTM"
    span = _fy_span_label(text)
    if span:
        return span
    quarter = _quarter_label(text)
    if quarter:
        return quarter
    fy = _FY_COLUMN.match(text)
    if fy:
        year = fy.group(1)[-2:]
        suffix = (fy.group(2) or "").lower()
        # Keep the projection marker in the label so a forecast column can never
        # be mistaken for reported history further down the pipeline.
        return f"FY{year}E" if suffix in _PROJECTED_SUFFIXES else f"FY{year}"
    match = re.search(r"(19|20)\d{2}", text)
    if match:
        return f"FY{match.group(0)[-2:]}"
    return text


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if pd.isna(value) else float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    if text in ("", "-", "NA", "nan", "#DIV/0!", "#VALUE!", "#REF!"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _find_sheet(book: dict[str, pd.DataFrame], key: str) -> pd.DataFrame | None:
    sheets = _find_sheets(book, key)
    return sheets[0][1] if sheets else None


def _find_sheets(book: dict[str, pd.DataFrame], key: str) -> list[tuple[str, pd.DataFrame]]:
    """
    Every sheet matching a key, best match first. An exact name match beats a
    prefix match beats a substring match, and within each, an earlier alias wins.
    Returning all candidates lets the caller skip a decoy (e.g. a 'Financials>'
    cover tab) and use the first sheet that actually parses.
    """
    wanted = SHEET_ALIASES[key]
    scored: list[tuple[int, int, str, pd.DataFrame]] = []
    for name, frame in book.items():
        n = _norm(name)
        for rank, alias in enumerate(wanted):
            if n == alias:
                scored.append((0, rank, name, frame)); break
            if n.startswith(alias):
                scored.append((1, rank, name, frame)); break
            if alias in n:
                scored.append((2, rank, name, frame)); break
    scored.sort(key=lambda x: (x[0], x[1]))
    return [(name, frame) for _, _, name, frame in scored]


def _matches_statement_alias(sheet_name: Any) -> bool:
    """True when a sheet name reads as one of the individual statement tabs."""
    nm = _norm(sheet_name)
    if not nm or "quarter" in nm:          # quarterly tabs are not FY periods
        return False
    for alias in STATEMENT_SHEET_ALIASES:
        if len(alias) < _SHORT_ALIAS_LEN:
            if nm == alias:                # "pl" / "pnl" only as the whole name
                return True
        elif nm.startswith(alias) or alias in nm:
            return True
    return False


# --------------------------------------------------------------------------
# workbook format detection
# --------------------------------------------------------------------------
# What kind of workbook was uploaded. Detection is by NORMALIZED sheet name, so
# "Profit & Loss" / "Profit and Loss" / "P&L" / "Income Statement" all count as
# the same thing.
FORMAT_HISTORICAL = "fundacheck_historical"   # HistoricalFS + Ratio Analysis …
FORMAT_STATEMENTS = "screener_statements"     # P&L / Balance Sheet / Cash Flow
FORMAT_DATA_SHEET = "data_sheet"              # only the raw Data Sheet is usable
FORMAT_UNSUPPORTED = "unsupported"

FORMAT_LABELS = {
    FORMAT_HISTORICAL: "FundaCheck / HistoricalFS workbook",
    FORMAT_STATEMENTS: "Screener-style separate statements workbook",
    FORMAT_DATA_SHEET: "raw workbook (Data Sheet only)",
    FORMAT_UNSUPPORTED: "unsupported workbook",
}


def detect_format(book: dict[str, pd.DataFrame]) -> str:
    """Classify an open workbook into one of the four supported shapes.

    A workbook can hold several of these at once (every Screener export carries
    a Data Sheet). The most structured shape that actually holds *values* wins,
    because that is the one the parser will read first.
    """
    if not book:
        return FORMAT_UNSUPPORTED

    def _has_values(frame: pd.DataFrame, min_concepts: int = 0) -> bool:
        try:
            _f, _s, _t = _parse_statement_sheet(frame)
        except ParseError:
            return False
        if _f.empty:
            return False
        if min_concepts:
            return len(SYN.find_all([str(i) for i in _f.index])) >= min_concepts
        return True

    if any(_has_values(f, MIN_STATEMENT_CONCEPTS)
           for _n, f in _find_sheets(book, "historical")):
        return FORMAT_HISTORICAL
    if any(_has_values(f) for name, f in book.items()
           if _matches_statement_alias(name)):
        return FORMAT_STATEMENTS

    data_sheet = _find_sheet(book, "data")
    if data_sheet is not None and not _parse_data_sheet_statements(data_sheet).empty:
        return FORMAT_DATA_SHEET

    # No values anywhere, but the *shape* is still recognisable — report the
    # shape so the error message can say what was expected and what was missing.
    if _find_sheets(book, "historical"):
        return FORMAT_HISTORICAL
    if any(_matches_statement_alias(name) for name in book):
        return FORMAT_STATEMENTS
    if data_sheet is not None:
        return FORMAT_DATA_SHEET
    return FORMAT_UNSUPPORTED


def _first_parsable(candidates: list[tuple[str, pd.DataFrame]],
                    min_concepts: int = 0):
    """First candidate sheet that parses to a non-empty frame; else empties.

    `min_concepts` guards against decoy tabs: a sheet only counts as the
    combined statements when the shared synonym layer recognises at least that
    many canonical financial lines on it. Without the gate, a "Revenue Model" or
    "DCF Inputs" tab (which parses fine, but holds segment build-ups rather than
    a P&L) would be accepted as the company's statements.
    """
    for _name, raw in candidates:
        try:
            frame, sections, title = _parse_statement_sheet(raw)
        except ParseError:
            continue
        if frame.empty:
            continue
        if min_concepts:
            found = SYN.find_all([str(i) for i in frame.index])
            if len(found) < min_concepts:
                SYN.LOGGER.debug("skipping sheet %r: only %d recognised "
                                 "financial lines", _name, len(found))
                continue
        return frame, sections, title
    return pd.DataFrame(), {}, ""


# --------------------------------------------------------------------------
# the actual sheet parser
# --------------------------------------------------------------------------
def _parse_statement_sheet(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str], str]:
    """
    Convert one raw sheet into (values DataFrame, metric->section map, title).

    The returned DataFrame is indexed by metric label with one column per year.
    """
    title = ""
    header_row = None
    year_labels: list[str] = []
    first_value_col = None

    # Grab a title-ish line (used only for the company name) from the top rows.
    for row_idx in range(min(len(raw), 6)):
        for cell in raw.iloc[row_idx]:
            if cell is None or (isinstance(cell, float) and pd.isna(cell)):
                continue
            text = str(cell).strip()
            # A title line, not a footnote: long enough to be a name, carrying a
            # separator, and free of the punctuation that marks a note/formula.
            if (12 < len(text) <= 90 and ("-" in text or "|" in text)
                    and not any(ch in text for ch in ";=%")
                    and not _looks_period(text)):
                title = title or text
            break

    # Find the header row by the *data*, not by a literal "Year" label: the first
    # row that holds three or more reporting-period cells (dates like 2017-03-31,
    # or FY24 / TTM). Its first period cell marks the first value column, and the
    # column immediately to its left holds the metric labels. This locates the
    # grid wherever it sits and whatever the header cell is called ("Year",
    # "Years", "Report Date", or nothing at all).
    # The BEST row wins, not merely the first one with three period cells: an
    # analyst-built model can carry a stray row of round numbers above the real
    # header, and the real header always holds more period cells than a fluke.
    # Ties go to the earliest row.
    best_row, best_cols = None, []
    for row_idx in range(min(len(raw), 25)):
        row = raw.iloc[row_idx]
        period_cols = [c for c in range(len(row)) if _looks_period(row.iloc[c])]
        if len(period_cols) >= 3 and len(period_cols) > len(best_cols):
            best_row, best_cols = row_idx, period_cols

    if best_row is not None:
        header_row = best_row
        first_value_col = best_cols[0]
        for value in raw.iloc[best_row].iloc[first_value_col:]:
            if value is None or (isinstance(value, float) and pd.isna(value)):
                year_labels.append("")
            else:
                year_labels.append(_year_label(value))

    if header_row is None:
        raise ParseError("Could not find a row of reporting periods on this sheet.")

    # Drop trailing empty year columns.
    while year_labels and year_labels[-1] == "":
        year_labels.pop()
    n_years = len(year_labels)
    label_col = max(0, first_value_col - 1)

    records: dict[str, list[float | None]] = {}
    sections: dict[str, str] = {}
    current_section = "GENERAL"

    for row_idx in range(header_row + 1, len(raw)):
        row = raw.iloc[row_idx]
        marker = row.iloc[label_col - 1] if label_col >= 1 else None
        label_cell = row.iloc[label_col]

        if label_cell is None or (isinstance(label_cell, float) and pd.isna(label_cell)):
            continue
        label = _clean_label(label_cell)
        if not label:
            continue

        # A "#" in the column left of the labels marks a section heading.
        if marker is not None and str(marker).strip() == "#":
            current_section = label.upper()
            continue

        values = [_to_float(v) for v in row.iloc[first_value_col:first_value_col + n_years]]
        if all(v is None for v in values):
            continue

        # Duplicate labels (e.g. "Total") get a suffix so nothing is lost.
        unique_label = label
        suffix = 2
        while unique_label in records:
            unique_label = f"{label} ({suffix})"
            suffix += 1

        records[unique_label] = values
        sections[unique_label] = current_section

    frame = pd.DataFrame.from_dict(records, orient="index", columns=year_labels)
    frame = frame.loc[:, [c for c in frame.columns if _is_period(c)]]
    # Templates often end with a decorative sparkline column ("TREND") that
    # holds no values. Drop anything completely empty.
    frame = frame.dropna(axis=1, how="all")
    return frame, sections, title


def _year_sort_key(col: str):
    if _norm(col) in ("ttm", "ltm"):
        return (9999,)
    match = re.search(r"\d+", str(col))
    return (int(match.group()) if match else 0,)


def _combine_statement_sheets(book: dict[str, pd.DataFrame]):
    """
    Build one historical frame from separate statement tabs (Income Statement,
    Profit & Loss, Balance Sheet, Cash Flow) when a workbook has no single
    combined sheet. Rows are stacked; duplicate labels keep the first seen.
    Returns (frame, metric->section map, title).
    """
    frames: list[pd.DataFrame] = []
    sections: dict[str, str] = {}
    title = ""
    for name, raw in book.items():
        if not _matches_statement_alias(name):
            continue
        try:
            frame, secs, sheet_title = _parse_statement_sheet(raw)
        except ParseError:
            continue
        if frame.empty:
            continue
        frames.append(frame)
        sections.update(secs)
        title = title or sheet_title
    if not frames:
        return pd.DataFrame(), {}, ""
    combined = pd.concat(frames)
    combined = combined[~combined.index.duplicated(keep="first")]
    combined = combined.loc[:, ~combined.columns.duplicated()]
    combined = combined.reindex(
        sorted(combined.columns, key=_year_sort_key), axis=1
    ).dropna(axis=1, how="all")
    return combined, sections, title


def _parse_data_sheet(raw: pd.DataFrame) -> dict[str, Any]:
    """Pull the small 'META' block (share count, price, market cap) if present."""
    meta: dict[str, Any] = {}
    wanted = {
        "numberofshares": "shares_outstanding",
        "facevalue": "face_value",
        "currentprice": "current_price",
        "marketcapitalization": "market_cap",
        "companyname": "company",
    }
    for _, row in raw.iterrows():
        cells = [c for c in row.tolist() if c is not None and not (isinstance(c, float) and pd.isna(c))]
        if len(cells) < 2:
            continue
        key = _norm(cells[0])
        if key in wanted:
            target = wanted[key]
            meta[target] = str(cells[1]).strip() if target == "company" else _to_float(cells[1])
    return meta


# Words that name a STATEMENT, not a company — dropped from a title line so
# "ADANI ENTERPRISES LIMITED | CONSOLIDATED INCOME STATEMENT" yields the company.
_TITLE_NOISE = ("income statement", "balance sheet", "cash flow statement",
                "cash flow", "profit and loss", "profit & loss",
                "historical financial data", "common size statement",
                "financial statements", "consolidated", "standalone")


# Tokens that mark the rest of a FILE NAME as versioning/noise rather than the
# company: "ITC Day 10 bcm.xlsx" -> "ITC", "TCS_model_v3_final" -> "TCS".
_FILENAME_NOISE = {
    "day", "days", "week", "model", "modelling", "modeling", "financial",
    "financials", "fs", "bcm", "dcf", "valuation", "final", "draft", "copy",
    "new", "old", "updated", "update", "version", "ver", "v1", "v2", "v3",
    "template", "workbook", "analysis", "sheet", "data", "raw", "export",
    "q1", "q2", "q3", "q4", "fy", "annual", "quarterly", "ltd", "limited",
}


def company_from_filename(filename: str) -> str:
    """Best-effort company name from an uploaded file's name.

    Keeps the leading words up to the first versioning/noise token, so
    "ITC Day 10 bcm.xlsx" reads as "ITC" and "Asian Paints FY25 model.xlsx" as
    "Asian Paints". Returns "" when nothing usable is left — the caller then
    keeps "Unknown Company" rather than inventing one.
    """
    stem = re.split(r"[\/]", str(filename))[-1]
    stem = re.sub(r"\.(xlsx|xlsm|xls|csv)$", "", stem, flags=re.I)
    stem = re.sub(r"[_\-]+", " ", stem)
    kept: list[str] = []
    for token in stem.split():
        low = token.lower()
        if low in _FILENAME_NOISE or re.fullmatch(r"[\d.()]+", token)                 or re.fullmatch(r"(?i)v\d+", token)                 or re.fullmatch(r"(19|20)\d{2}", token)                 or re.fullmatch(r"(?i)(fy|q[1-4])\s?\d{2,4}", token)                 or re.fullmatch(r"(?i)\d{2,4}\s?-\s?\d{2,4}", token):
            break
        kept.append(token)
        if len(kept) >= 4:
            break
    return " ".join(kept).strip()


def _company_from_title(title: str) -> str:
    """'Historical Financial Data - ADANI ENTERPRISES LTD' -> 'Adani Enterprises Ltd'.

    Splits on '-' or '|' and keeps the part that is not a statement name, so
    both the Screener title row and a "COMPANY | CONSOLIDATED INCOME STATEMENT"
    banner resolve to the company.
    """
    parts = [p.strip() for p in re.split(r"[|\-]", str(title)) if p.strip()]
    if not parts:
        return "Unknown Company"

    def _is_noise(part: str) -> bool:
        low = part.lower()
        return any(word in low for word in _TITLE_NOISE)

    named = [p for p in parts if not _is_noise(p)]
    chosen = named[-1] if named else parts[-1]
    return _clean_label(chosen).title() or "Unknown Company"



# --------------------------------------------------------------------------
# rebuilding statements from the raw Data Sheet
# --------------------------------------------------------------------------
# The derived sheets (HistoricalFS, Ratio Analysis) are grids of formulas. Some
# exports carry no cached results, so those sheets read as empty even though the
# Data Sheet next to them holds every raw number. This rebuilds the statements
# from those raw values so such a workbook still analyses.
DATA_SHEET_ALIASES: dict[str, str] = {
    "debtors": "Receivables",
    "tradepayables": "Trade Payables",
    "sundrycreditors": "Trade Payables",
    "creditors": "Trade Payables",
    "accountspayable": "Trade Payables",
    "dividendamount": "Dividend Amount",
    "adjustedequitysharesincr": "Adjusted Equity Shares",
    "sales": "Sales",
    "netprofit": "Net Profit",
    "profitbeforetax": "Earnings Before Tax",
    "tax": "Tax",
    "interest": "Interest",
    "depreciation": "Depreciation",
    "otherincome": "Other Income",
    "equitysharecapital": "Equity Share Capital",
    "reserves": "Reserves",
    "borrowings": "Borrowings",
    "otherliabilities": "Other Liabilities",
    "netblock": "Net Block",
    "capitalworkinprogress": "Capital Work in Progress",
    "investments": "Investments",
    "receivables": "Receivables",
    "inventory": "Inventory",
    "cashbank": "Cash & Bank",
    "cashfromoperatingactivity": "Cash from Operating Activity",
    "cashfrominvestingactivity": "Cash from Investing Activity",
    "cashfromfinancingactivity": "Cash from Financing Activity",
    "netcashflow": "Net Cash Flow",
    "noofequityshares": "No of Equity Shares",
}

# Everything that sits above EBITDA in an Indian P&L. Split into the two groups
# Screener's own HistoricalFS template uses, so the rebuilt statement shows the
# same lines the workbook would. Grouping is by ROW LABEL (never by cell
# position), so it holds for every Screener model regardless of row order.
#   COGS  = Raw Material + Power & Fuel + Other Mfr + Employee − Change in Inventory
#   S&G   = Selling & admin + Other Expenses
# "Change in Inventory" is a contra-expense (a stock build is production not yet
# sold), so it is SUBTRACTED from the cost base, not added.
COGS_COST_KEYS = ("rawmaterialcost", "powerandfuel", "othermfrexp",
                  "employeecost", "expenses")
SGA_COST_KEYS = ("sellingandadmin", "otherexpenses")
CHANGE_IN_INVENTORY_KEY = "changeininventory"
OPERATING_COST_KEYS = (
    *COGS_COST_KEYS, *SGA_COST_KEYS, CHANGE_IN_INVENTORY_KEY,
)

# Concepts the Data-Sheet rebuild is willing to accept from the shared synonym
# layer when a row label is not one of Screener's own fixed labels above. This
# is how a raw workbook that spells a line differently ("Revenue from
# Operations", "Finance Costs", "Trade Payables") still rebuilds — the mapping
# lives in core/synonyms.py, not scattered here.
DATA_SHEET_CONCEPTS: dict[str, str] = {
    "sales": "Sales",
    "other_income": "Other Income",
    "depreciation": "Depreciation",
    "interest": "Interest",
    "profit_before_tax": "Earnings Before Tax",
    "tax": "Tax",
    "net_profit": "Net Profit",
    "equity_share_capital": "Equity Share Capital",
    "reserves": "Reserves",
    "borrowings": "Borrowings",
    "other_liabilities": "Other Liabilities",
    "payables": "Trade Payables",
    "net_block": "Net Block",
    "cwip": "Capital Work in Progress",
    "investments": "Investments",
    "receivables": "Receivables",
    "inventory": "Inventory",
    "cash": "Cash & Bank",
    "cfo": "Cash from Operating Activity",
    "cfi": "Cash from Investing Activity",
    "cff": "Cash from Financing Activity",
    "net_cash_flow": "Net Cash Flow",
    "share_count": "No of Equity Shares",
    "adjusted_share_count": "Adjusted Equity Shares",
    "dividend_amount": "Dividend Amount",
}


def _parse_data_sheet_statements(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Read the Data Sheet's raw blocks into one metric-by-year frame.

    Each block ("PROFIT & LOSS", "BALANCE SHEET", "CASH FLOW:") restates its own
    "Report Date" header, so the year columns are picked up per block. The
    quarterly block is skipped: its dates are quarters, not financial years.
    """
    records: dict[str, dict[str, float]] = {}
    costs: dict[str, dict[str, float]] = {}
    years: list[str] = []
    in_quarters = False

    for _, row in raw.iterrows():
        cells = row.tolist()
        label_cell = next(
            (c for c in cells if c is not None and not (isinstance(c, float) and pd.isna(c))),
            None,
        )
        if label_cell is None:
            continue
        label = _clean_label(label_cell)
        key = _norm(label)
        start = cells.index(label_cell) + 1

        if key in ("quarters",):
            in_quarters = True
            continue
        if key == "price":
            # The PRICE: row is both a block marker and a data row — Screener
            # prints the year-end share price for each annual column on it.
            in_quarters = False
            if years:
                values = [_to_float(v) for v in cells[start:start + len(years)]]
                row_map = {y: v for y, v in zip(years, values) if v is not None}
                if row_map:
                    records.setdefault("Price", {}).update(row_map)
            continue
        if key in ("balancesheet", "cashflow", "profitloss", "derived", "meta"):
            in_quarters = False
            continue
        if key == "reportdate":
            if not in_quarters:
                years = [
                    _year_label(v) for v in cells[start:]
                    if v is not None and not (isinstance(v, float) and pd.isna(v))
                ]
            continue
        if in_quarters or not years:
            continue

        values = [_to_float(v) for v in cells[start:start + len(years)]]
        if all(v is None for v in values):
            continue
        row_map = {y: v for y, v in zip(years, values) if v is not None}

        if key in OPERATING_COST_KEYS:
            costs[label] = row_map
        elif key in DATA_SHEET_ALIASES:
            records.setdefault(DATA_SHEET_ALIASES[key], {}).update(row_map)
        else:
            # Not one of Screener's fixed labels — fall back to the shared
            # synonym layer, which only answers when it is confident.
            concept = SYN.resolve(label)
            target = DATA_SHEET_CONCEPTS.get(concept) if concept else None
            if target:
                records.setdefault(target, {}).update(row_map)

    if not records:
        return pd.DataFrame()

    frame = pd.DataFrame.from_dict(records, orient="index")
    frame = frame.loc[:, [c for c in frame.columns if _is_period(c)]]
    frame = frame.reindex(sorted(frame.columns, key=lambda c: (len(c), c)), axis=1)

    # EBITDA is not reported directly: it is sales less the operating cost lines.
    # Rebuild COGS and Selling & General Expenses as separate lines (the same
    # split the Screener HistoricalFS template makes) so every input line is
    # preserved, then derive Gross Profit, EBITDA and EBIT from them.
    if costs and "Sales" in frame.index:
        cost_frame = pd.DataFrame.from_dict(costs, orient="index").reindex(
            columns=frame.columns
        )
        cogs_rows, sga_rows, change = [], [], None
        for label in cost_frame.index:
            key = _norm(label)
            if key == CHANGE_IN_INVENTORY_KEY:
                change = cost_frame.loc[label]
            elif key in SGA_COST_KEYS:
                sga_rows.append(label)
            else:                                   # every other operating cost is COGS
                cogs_rows.append(label)

        zero = pd.Series(0.0, index=cost_frame.columns)
        cogs = cost_frame.loc[cogs_rows].sum(axis=0, min_count=1) if cogs_rows else zero.copy()
        if change is not None:                       # contra-expense: subtract it
            cogs = cogs.sub(change, fill_value=0.0)
        sga = cost_frame.loc[sga_rows].sum(axis=0, min_count=1) if sga_rows else None

        frame.loc["COGS"] = cogs
        if sga is not None:
            frame.loc["Selling & General Expenses"] = sga
        total_cost = cogs.add(sga, fill_value=0.0) if sga is not None else cogs
        frame.loc["Gross Profit"] = frame.loc["Sales"].sub(cogs, fill_value=0.0)
        frame.loc["EBITDA"] = frame.loc["Sales"].sub(total_cost, fill_value=0.0)
        if "Depreciation" in frame.index:
            frame.loc["EBIT (OPM)"] = frame.loc["EBITDA"] - frame.loc["Depreciation"]

    if "Equity Share Capital" in frame.index and "Reserves" in frame.index:
        _liability_lines = ["Equity Share Capital", "Reserves", "Borrowings",
                            "Other Liabilities", "Trade Payables"]
        frame.loc["Total Asset"] = frame.reindex(
            _liability_lines).sum(axis=0, min_count=1)

        # "Other Assets" is the remainder of the asset side that the Data Sheet
        # does not itemise, so the assets shown add up to the balance-sheet total
        # (Total Assets == Total Liabilities & Equity). Derived as the plug, it
        # holds for any workbook without depending on cell positions.
        _asset_lines = ["Net Block", "Capital Work in Progress", "Investments",
                        "Receivables", "Inventory", "Cash & Bank"]
        present = [a for a in _asset_lines if a in frame.index]
        if present:
            listed = frame.loc[present].sum(axis=0, min_count=1)
            other = frame.loc["Total Asset"].sub(listed, fill_value=0.0)
            # Ignore rounding dust; only surface a genuine residual.
            if other.abs().max() and float(other.abs().max()) > 1.0:
                frame.loc["Other Assets"] = other

    # Share count: Screener's Data Sheet carries the raw count AND a
    # bonus/split-adjusted count in crore ("Adjusted Equity Shares in Cr"). The
    # adjusted series is the one the HistoricalFS template divides by, because a
    # bonus issue is not new economic capital — using the raw count overstates
    # EPS in the years before a bonus. Prefer adjusted whenever it is present.
    raw_shares = None
    if "No of Equity Shares" in frame.index:
        raw_shares = frame.loc["No of Equity Shares"].replace(0, pd.NA)
        # Screener stores the absolute share count; the statements use crore.
        if raw_shares.dropna().max() and float(raw_shares.dropna().max()) > 1e6:
            raw_shares = raw_shares / 1e7
        frame.loc["No of Equity Shares"] = raw_shares

    shares = None
    if "Adjusted Equity Shares" in frame.index:
        adjusted = frame.loc["Adjusted Equity Shares"].replace(0, pd.NA)
        if adjusted.dropna().max() and float(adjusted.dropna().max()) > 1e6:
            adjusted = adjusted / 1e7
        frame.loc["Adjusted Equity Shares"] = adjusted
        if not adjusted.dropna().empty:
            shares = adjusted
    if shares is None:
        shares = raw_shares

    if "Net Profit" in frame.index and shares is not None:
        frame.loc["Earnings per Share"] = frame.loc["Net Profit"] / shares
        if "Dividend Amount" in frame.index:
            frame.loc["Dividend per Share"] = frame.loc["Dividend Amount"] / shares

    return frame.dropna(axis=1, how="all")


# Lines the Data-Sheet rebuild COMPUTES rather than reads. Everything else it
# produces came straight off the sheet. Used for provenance (Part 8).
REBUILD_DERIVED_LINES: dict[str, str] = {
    "COGS": "Raw Material + Power & Fuel + Other Mfr + Employee − Change in Inventory",
    "Selling & General Expenses": "Selling & admin + Other Expenses",
    "Gross Profit": "Sales − COGS",
    "EBITDA": "Sales − (COGS + Selling & General Expenses)",
    "EBIT (OPM)": "EBITDA − Depreciation",
    "Total Asset": "Equity Share Capital + Reserves + Borrowings + Other Liabilities",
    "Other Assets": "Total Assets − (Net Block + CWIP + Investments + Receivables "
                    "+ Inventory + Cash)",
    "Earnings per Share": "Net Profit ÷ adjusted equity shares",
    "Dividend per Share": "Dividend Amount ÷ adjusted equity shares",
}


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------
def _mark_rebuilt(model: FinancialModel, label: str) -> None:
    """Record whether a rebuilt Data-Sheet line was read or computed."""
    formula = REBUILD_DERIVED_LINES.get(label)
    if formula:
        model.mark_derived(label, formula)
    else:
        model.mark_source(label, "Data Sheet")


def load_model(source: Any, filename: str | None = None) -> FinancialModel:
    """
    Parse an uploaded 3-statement workbook.

    `source` can be a file path or any file-like object (which is what
    Streamlit's file uploader hands us). `filename` is the original upload name,
    used only as a LAST resort for the company name when no sheet carries a
    title row — an analyst-built model often has none.
    """
    book = pd.read_excel(source, sheet_name=None, header=None, engine="openpyxl")
    if not book:
        raise ParseError("The workbook appears to be empty.")

    model = FinancialModel()
    model.meta["workbook_format"] = detect_format(book)
    model.meta["workbook_format_label"] = FORMAT_LABELS[model.meta["workbook_format"]]
    model.meta["sheets"] = list(book)
    title = ""

    # Try every sheet that could be the combined statements, best match first,
    # and use the first that actually parses (skips decoys like a 'Financials>'
    # cover tab). If none do, stitch separate statement tabs together.
    model.historical, sections, title = _first_parsable(
        _find_sheets(book, "historical"), min_concepts=MIN_STATEMENT_CONCEPTS)
    if model.historical.empty:
        model.historical, sections, title = _combine_statement_sheets(book)
    model.sections.update(sections)
    model.years = list(model.historical.columns)
    for label in model.historical.index:
        model.mark_source(label, "statement sheet")

    ratios, ratio_sections, ratio_title = _first_parsable(_find_sheets(book, "ratios"))
    if not ratios.empty:
        model.ratios = ratios
        model.sections.update(ratio_sections)
        for label in ratios.index:
            model.mark_source(label, "Ratio Analysis sheet")
        title = title or ratio_title

    common_size, cs_sections, _cs_title = _first_parsable(_find_sheets(book, "common_size"))
    if not common_size.empty:
        model.common_size = common_size
        for label, section in cs_sections.items():
            model.sections.setdefault(label, section)
        for label in common_size.index:
            model.provenance.setdefault(
                label, {"kind": "source", "formula": "",
                        "where": "Common Size sheet"})

    data_sheet = _find_sheet(book, "data")
    if data_sheet is not None:
        model.meta.update(_parse_data_sheet(data_sheet))

        # The Data Sheet holds the raw numbers. Use it two ways: if the formula
        # sheets parsed to almost nothing, rebuild wholesale; otherwise just fill
        # in any lines the statement sheets were missing (e.g. a workbook with a
        # Balance Sheet tab but no Profit & Loss tab still gets Sales / Net Profit).
        rebuilt = _parse_data_sheet_statements(data_sheet)
        if not rebuilt.empty:
            if len(model.historical) < 8:
                model.historical = rebuilt
                model.years = list(rebuilt.columns)
                model.rebuilt_from_data_sheet = True
                for label in rebuilt.index:
                    model.sections.setdefault(label, "REBUILT FROM DATA SHEET")
                    _mark_rebuilt(model, label)
            else:
                missing = [l for l in rebuilt.index if l not in model.historical.index]
                if missing:
                    extra = rebuilt.loc[missing].reindex(columns=model.historical.columns)
                    extra = extra.dropna(how="all")
                    model.historical = pd.concat([model.historical, extra])
                    for label in extra.index:
                        model.sections.setdefault(label, "REBUILT FROM DATA SHEET")
                        _mark_rebuilt(model, label)

    if model.historical.empty:
        seen = ", ".join(str(s) for s in book) or "none"
        shape = FORMAT_LABELS.get(model.meta.get("workbook_format", ""),
                                  "unsupported workbook")
        raise ParseError(
            f"Could not read any financial statements from this workbook "
            f"(detected shape: {shape}; sheets found: {seen}). It needs either a "
            "combined statements sheet (like 'HistoricalFS'), separate "
            "Profit & Loss / Balance Sheet / Cash Flow tabs, or a Screener "
            "'Data Sheet' — each with a row of yearly reporting dates."
        )

    company = model.meta.get("company") or _company_from_title(title)
    # A title scraped off a statement sheet can be a section heading rather than
    # a name ("Current Assets"). If it matches a line label we already parsed, it
    # is not the company — fall back to the file name.
    scraped_is_a_row = any(
        _norm(company) == _norm(label) for label in model.historical.index)
    if (company in ("", "Unknown Company") or scraped_is_a_row) and filename:
        from_file = company_from_filename(filename)
        if from_file:
            company = from_file
            model.meta["company_source"] = "file name"
    elif scraped_is_a_row:
        company = "Unknown Company"
    model.company = company or "Unknown Company"
    return model
