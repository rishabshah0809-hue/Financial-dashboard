"""
Deterministic benchmark corpus for Indian quarterly-results PDF extraction.

WHAT THIS IS
------------
A manifest of real listed-company quarterly filings plus, for each one, the
structured output the parser is expected to produce.

PROVENANCE OF THE EXPECTED VALUES
---------------------------------
Nothing here is estimated, modelled or guessed. Every expected number was
transcribed by hand from the filing's own native text layer, and each entry
records the exact page it was read from (`truth_page`, 1-based) so any reviewer
can re-open the PDF and check the figure. Where a filing's results page is a
scanned image whose text layer is unreadable, NO numbers are recorded at all --
the entry instead declares `requires_ocr=True`. A missing number is never
replaced with a plausible one.

Amounts are stored in the filing's OWN reported unit (`reported_unit`), exactly
as printed. The parser normalises to crore internally; the benchmark converts
the expectation at comparison time rather than pre-baking a converted figure,
so a unit-handling regression shows up as a failure instead of hiding.

THE PDFs ARE NOT COMMITTED
--------------------------
They are multi-MB binaries and are covered by `tests/fixtures/*.pdf` in
.gitignore. Place them in `tests/fixtures/` under the `filename` given below,
or point $FUNDACHECK_BENCHMARK_DIR at a directory holding them. Any filing that
is absent is SKIPPED, never failed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# unit handling
# --------------------------------------------------------------------------
#: multiplier from a reported unit into crore (the parser's internal unit)
TO_CRORE = {
    "crore": 1.0,
    "lakh": 0.01,
    "million": 0.1,
    "thousand": 0.0001,
}


def to_crore(value: float, reported_unit: str) -> float:
    """Convert an as-printed figure into crore."""
    try:
        return value * TO_CRORE[reported_unit]
    except KeyError:                                    # pragma: no cover
        raise ValueError(f"unknown benchmark unit {reported_unit!r}")


# --------------------------------------------------------------------------
# corpus entry
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Filing:
    """One benchmark filing and its expected structured output."""

    key: str
    filename: str
    company_contains: str

    #: what the extractor must report as the section it used
    expected_scope: str                      # "consolidated" | "standalone"
    #: 1-based page of the results table the extractor SHOULD select
    expected_results_page: int
    #: page the expected numbers below were transcribed from
    truth_page: int

    expected_current_period: str
    expected_previous_period: str
    expected_current_tag: str
    expected_previous_tag: str

    #: the unit as printed in the filing
    reported_unit: str
    statement_type: str = "profit_and_loss"

    #: layout characteristics this filing is in the corpus to exercise
    covers: tuple[str, ...] = ()

    #: True when the results table is a scanned image: no native-text numbers
    #: can be transcribed, so `values` is deliberately empty.
    requires_ocr: bool = False

    #: {canonical row name: {period: as-printed value}} -- hand-transcribed
    values: dict[str, dict[str, float]] = field(default_factory=dict)

    #: why the filing is hard / what regression it guards against
    notes: str = ""

    # -- resolution ------------------------------------------------------
    def path(self) -> Path | None:
        """Locate the PDF, or None when it is not available locally."""
        roots = []
        env = os.environ.get("FUNDACHECK_BENCHMARK_DIR")
        if env:
            roots.append(Path(env))
        roots.append(Path(__file__).parents[1] / "fixtures")
        roots.append(Path.home() / "Downloads")
        for root in roots:
            candidate = root / self.filename
            if candidate.is_file():
                return candidate
        return None

    def expected(self, row: str, period: str) -> float | None:
        """Expected value for a row/period, in CRORE, or None if untranscribed."""
        raw = self.values.get(row, {}).get(period)
        return None if raw is None else to_crore(raw, self.reported_unit)


# --------------------------------------------------------------------------
# the corpus
# --------------------------------------------------------------------------
# Row keys below are the parser's canonical names (core.quarterly_semantics
# ROW_SYNONYMS targets), NOT the filing's own wording. The filing's own wording
# is quoted in `notes` wherever it is unusual, because "does the alias layer map
# this label" is exactly what several of these entries test.

CORPUS: tuple[Filing, ...] = (
    # ----------------------------------------------------------------- TCS
    Filing(
        key="tcs",
        filename="tcs.pdf",
        company_contains="TATA CONSULTANCY SERVICES",
        expected_scope="consolidated",
        expected_results_page=8,
        truth_page=8,
        expected_current_period="Jun-2026",
        expected_previous_period="Mar-2026",
        expected_current_tag="Q1 FY27",
        expected_previous_tag="Q4 FY26",
        reported_unit="crore",
        covers=(
            "normal text PDF",
            "consolidated + standalone in same filing",
            "month-first date headers",
            "negative values in parentheses",
            "exceptional-item layout",
            "segment page adjacent to the P&L",
            "OCI section below the P&L",
        ),
        values={
            "Sales":                     {"Jun-2026": 72275.0, "Mar-2026": 70698.0},
            "Other Income":              {"Jun-2026": 1568.0,  "Mar-2026": 757.0},
            "Total Income":              {"Jun-2026": 73843.0, "Mar-2026": 71455.0},
            "Employee Benefits Expense": {"Jun-2026": 42137.0, "Mar-2026": 40143.0},
            "Finance Costs":             {"Jun-2026": 273.0,   "Mar-2026": 265.0},
            "Depreciation":              {"Jun-2026": 1239.0,  "Mar-2026": 1406.0},
            "Other Expenses":            {"Jun-2026": 10228.0, "Mar-2026": 9835.0},
            "Total Expenses":            {"Jun-2026": 55231.0, "Mar-2026": 53093.0},
            "Earnings Before Tax":       {"Jun-2026": 17944.0, "Mar-2026": 18362.0},
            "Current Tax":               {"Jun-2026": 4722.0,  "Mar-2026": 4832.0},
            "Deferred Tax":              {"Jun-2026": -198.0,  "Mar-2026": -254.0},
            "Net Profit":                {"Jun-2026": 13420.0, "Mar-2026": 13784.0},
        },
        notes=(
            "Page 9 is 'Audited Consolidated Interim Segment Information' and "
            "page 8 is the real 'Statement of Financial Results' -- the extractor "
            "must not select the segment page. Below the P&L an OCI section "
            "contains 'Remeasurement of defined employee benefit plans' "
            "(193 / (121)); matching that row as Employee Benefits Expense "
            "instead of the real 'Employee benefit expenses' (42,137 / 40,143) "
            "is the regression this entry guards. 'Total comprehensive income' "
            "must be the TOTAL (13,673 / 14,445), not the "
            "'attributable to Shareholders of the Company' subtotal "
            "(13,609 / 14,354). Mar-2026 has no exceptional item -- the "
            "'-' cells must not pick up the 18,362 PBT figure."
        ),
    ),

    # --------------------------------------------------------------- Adani
    Filing(
        key="adani-enterprises",
        filename="adani-enterprises.pdf",
        company_contains="Adani Enterprises",
        expected_scope="consolidated",
        expected_results_page=11,
        truth_page=11,
        expected_current_period="Jun-2026",
        expected_previous_period="Mar-2026",
        expected_current_tag="Q1 FY27",
        expected_previous_tag="Q4 FY26",
        reported_unit="crore",
        covers=(
            "normal text PDF",
            "consolidated + standalone in same filing",
            "day-first dotted numeric dates (30-06-2026)",
            "Indian-number formatting",
            "two-decimal crore",
            "table spanning two pages",
            "exceptional-item layout",
        ),
        values={
            "Sales":        {"Jun-2026": 32923.98, "Mar-2026": 32439.31},
            "Other Income": {"Jun-2026": 622.28,   "Mar-2026": 747.80},
        },
        notes=(
            "'STATEMENT OF UNAUDITED CONSOLIDATED FINANCIAL RESULTS', "
            "'(₹ in Crores)', four columns: 30-06-2026 / 31-03-2026 / "
            "30-06-2025 / 31-03-2026(Year Ended). The year-ended column must be "
            "excluded. The standalone statement starts at page 18 and must lose "
            "to the consolidated one. Baseline defect: Exceptional Items for "
            "Mar-2026 was reported equal to Earnings Before Tax (728.81), i.e. "
            "one row's value leaked into another."
        ),
    ),

    # ----------------------------------------------------------------- BSE
    Filing(
        key="bse",
        filename="bse.pdf",
        company_contains="BSE",
        expected_scope="consolidated",
        expected_results_page=5,
        truth_page=5,
        expected_current_period="Jun-2026",
        expected_previous_period="Mar-2026",
        expected_current_tag="Q1 FY27",
        expected_previous_tag="Q4 FY26",
        reported_unit="lakh",
        covers=(
            "exchange-style financial statement",
            "units in ₹ lakh",
            "Indian-number formatting (1,56,602)",
            "negative values in parentheses",
            "numbered/lettered row labels (5a to 5f)",
            "discontinued-operation section",
            "industry-specific expense lines",
        ),
        values={
            "Sales":                        {"Jun-2026": 156602.0, "Mar-2026": 156351.0},
            "Other Income":                 {"Jun-2026": 552.0,    "Mar-2026": 497.0},
            "Total Income":                 {"Jun-2026": 170672.0, "Mar-2026": 163017.0},
            "Employee Benefits Expense":    {"Jun-2026": 8705.0,   "Mar-2026": 6352.0},
            "Other Expenses":               {"Jun-2026": 6316.0,   "Mar-2026": 10003.0},
            "Depreciation":                 {"Jun-2026": 4260.0,   "Mar-2026": 5479.0},
            "Total Expenses":               {"Jun-2026": 53669.0,  "Mar-2026": 55694.0},
            "Share of Profit of Associates": {"Jun-2026": 1954.0,  "Mar-2026": 1094.0},
            "Earnings Before Tax":          {"Jun-2026": 116366.0, "Mar-2026": 106345.0},
            "Net Profit":                   {"Jun-2026": 87266.0,  "Mar-2026": 79547.0},
            "Total Comprehensive Income":   {"Jun-2026": 87227.0,  "Mar-2026": 81589.0},
        },
        notes=(
            "'Statement of Consolidated Financial Results', '(₹ In Lakhs)'. "
            "Revenue is split across 'Revenue from operations' (1,56,602), "
            "'Investment income' (13,518) and 'Other income' (552); Total income "
            "(1,70,672) is the sum of all three, so Total Income - Sales - Other "
            "Income does NOT close without the investment-income line. "
            "Baseline defect: Earnings Before Tax was reported as 87,266, which "
            "is in fact 'Net profit after tax' -- the real PBT is 1,16,366 on "
            "line 10. Total comprehensive income must be the 87,227 total, not "
            "the 87,377 'attributable to shareholders' subtotal."
        ),
    ),

    # ---------------------------------------------------------- Anand Rathi
    Filing(
        key="anand-rathi",
        filename="anand-rathi.pdf",
        company_contains="ANAND RATHI WEALTH",
        # The consolidated statement (page 7) is a degraded scan whose text
        # layer is unusable. Falling back to the standalone statement and
        # LABELLING it standalone is the correct behaviour here.
        expected_scope="standalone",
        expected_results_page=11,
        truth_page=11,
        expected_current_period="Jun-2026",
        expected_previous_period="Mar-2026",
        expected_current_tag="Q1 FY27",
        expected_previous_tag="Q4 FY26",
        reported_unit="lakh",
        covers=(
            "standalone fallback when consolidated is unreadable",
            "image/scanned table (consolidated section)",
            "mixed text + image filing",
            "units in ₹ lakh",
            "NBFC / wealth-management statement",
            "discontinued-operation section",
        ),
        values={},
        notes=(
            "Page 7 IS the consolidated statement ('STATEMENT OF UNAUDITED "
            "CONSOLIDATED RESULT ...', '(In INR Lakhs)') but it is a poor scan: "
            "pdfplumber returns mangled glyphs, so no figure can be transcribed "
            "from it honestly and none is recorded. The engine must therefore "
            "fall back to the standalone statement and report scope="
            "'standalone' with a visible warning -- it must NOT silently present "
            "standalone figures as consolidated, and must NOT hard-fail. "
            "Values are intentionally empty: structural expectations only until "
            "an OCR engine is available."
        ),
    ),

    # ----------------------------------------------------------------- BEL
    Filing(
        key="bel",
        filename="bel.pdf",
        company_contains="BHARAT ELECTRONICS",
        expected_scope="consolidated",
        expected_results_page=9,
        truth_page=9,
        expected_current_period="Jun-2026",
        expected_previous_period="Mar-2026",
        expected_current_tag="Q1 FY27",
        expected_previous_tag="Q4 FY26",
        reported_unit="lakh",
        requires_ocr=True,
        covers=(
            "image/scanned table",
            "bilingual (Hindi + English) filing",
            "units in ₹ lakh",
            "PSU / defence-manufacturer statement",
        ),
        values={},
        notes=(
            "Page 9 is 'B. Consolidated Results' / '(₹ in Lakhs)' but the whole "
            "filing is a scan: the text layer is transliterated noise "
            "('tfiRd THRSTTRHR'). Currently the extractor raises "
            "QuarterlyPDFError. That is ACCEPTABLE (an explicit, explained "
            "failure beats invented numbers) but not the goal -- this entry is "
            "the OCR target. No values are transcribed because none can be read "
            "from the text layer."
        ),
    ),

    # ---------------------------------------------------------------- Jash
    Filing(
        key="jash-engineering",
        filename="jash-engineering.pdf",
        company_contains="Jash",
        expected_scope="consolidated",
        expected_results_page=0,          # unknown until OCR can locate it
        truth_page=0,
        expected_current_period="Jun-2026",
        expected_previous_period="Mar-2026",
        expected_current_tag="Q1 FY27",
        expected_previous_tag="Q4 FY26",
        reported_unit="lakh",
        requires_ocr=True,
        covers=(
            "image/scanned table",
            "small-cap filing",
            "results table not present in the text layer",
        ),
        values={},
        notes=(
            "No statement page is visible in the native text layer beyond the "
            "auditor letters (pages 1-6), so the results table is image-only. "
            "Extractor currently raises QuarterlyPDFError -- correct for now. "
            "expected_results_page is 0 ('unknown'): it must not be asserted "
            "until an OCR engine can locate the table, because guessing it "
            "would be fabrication."
        ),
    ),

    # ------------------------------------------------------------ Reliance
    Filing(
        key="reliance",
        filename="reliance-quarterly-results.pdf",
        company_contains="Reliance Industries",
        expected_scope="consolidated",
        expected_results_page=9,
        truth_page=9,
        expected_current_period="Jun-2026",
        expected_previous_period="Mar-2026",
        expected_current_tag="Q1 FY27",
        expected_previous_tag="Q4 FY26",
        reported_unit="crore",
        covers=(
            "mixed text + image table (one column rasterised)",
            "consolidated + standalone in same filing",
            "day-first apostrophe dates (30th Jun'26)",
            "integer crore",
            "GST / excise-duty lines",
            "reported-ratio block used for validation",
        ),
        values={
            # Mar-2026 column is native text on page 9; the Jun-2026 column on
            # the same page is a rasterised image and is therefore NOT
            # transcribed here -- it is covered by the OCR test path.
            "Sales":                     {"Mar-2026": 298621.0},
            "Other Income":              {"Mar-2026": 4447.0},
            "Total Income":              {"Mar-2026": 303068.0},
            "Cost of Materials Consumed": {"Mar-2026": 128985.0},
            "Employee Benefits Expense": {"Mar-2026": 7683.0},
            "Finance Costs":             {"Mar-2026": 6585.0},
            "Depreciation":              {"Mar-2026": 14808.0},
            "Other Expenses":            {"Mar-2026": 41193.0},
            "Total Expenses":            {"Mar-2026": 275873.0},
            "Earnings Before Tax":       {"Mar-2026": 27195.0},
            "Current Tax":               {"Mar-2026": 844.0},
            "Deferred Tax":              {"Mar-2026": 5735.0},
            "Net Profit":                {"Mar-2026": 20589.0},
        },
        notes=(
            "The historic reference filing. The Jun-2026 column is an image, so "
            "without OCR every Jun-2026 cell is correctly None. Values for the "
            "OCR'd column live in tests/test_quarterly_pdf.py::RelianceAcceptance "
            "and are not duplicated here."
        ),
    ),
)


#: layout dimensions the corpus is meant to span (Phase 1 requirement list),
#: mapped to the filings that exercise each one. Asserted by the benchmark so
#: that removing a filing surfaces the resulting coverage hole.
REQUIRED_COVERAGE: dict[str, tuple[str, ...]] = {
    "normal text PDF":                      ("tcs", "adani-enterprises"),
    "consolidated + standalone in same filing": ("tcs", "adani-enterprises", "reliance"),
    "standalone-only / standalone fallback": ("anand-rathi",),
    "image/scanned table":                  ("bel", "jash-engineering"),
    "mixed text + image table":             ("reliance", "anand-rathi"),
    "different column ordering":            ("bse", "adani-enterprises"),
    "different date/header wording":        ("tcs", "adani-enterprises", "bse", "reliance"),
    "Indian-number formatting":             ("bse", "adani-enterprises"),
    "negative values in parentheses":       ("tcs", "bse"),
    "units ₹ crore":                        ("tcs", "adani-enterprises", "reliance"),
    "units ₹ lakh":                         ("bse", "anand-rathi", "bel"),
    "exceptional-item layouts":             ("tcs", "adani-enterprises"),
    "bank/NBFC/exchange-style statements":  ("bse", "anand-rathi"),
}


def by_key(key: str) -> Filing:
    for filing in CORPUS:
        if filing.key == key:
            return filing
    raise KeyError(key)                                 # pragma: no cover


def available() -> list[Filing]:
    """Corpus entries whose PDF is present on this machine."""
    return [f for f in CORPUS if f.path() is not None]
