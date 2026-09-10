# Quarterly-PDF ingestion — implementation report

Adds quarterly-results PDF ingestion to FundaCheck as an **additional input
mode**, without disturbing the existing Excel/annual workflow. The uploaded PDF
is the primary source; every number is either read from the PDF, calculated from
PDF values in Python, taken from the PDF's own ratio table, or left unavailable.

## Source-of-truth hierarchy

```
        PDF raw values
                │   (extracted from the text layer, or OCR'd from a
                │    rasterised column; validated by accounting identities)
                ▼
        Python deterministic calculations
                │   (ratios/margins/growth computed here, in Python)
                ▼
        PDF-reported ratios  ──►  used to VALIDATE the Python result when
                │                 Python can compute it; used as the VALUE only
                │                 when Python cannot (missing input)
                ▼
        Screener fallback
                │   (consulted ONLY for inputs the PDF lacks; an annual/TTM
                │    value is rejected — it can never represent a quarter)
                ▼
        Unavailable  (never invented, never estimated)
```

Guarantees enforced in code:

- **The LLM never computes a financial number.** All arithmetic is in
  `core/quarterly_pdf.py` (`derive_quarterly`) — deterministic Python.
- **Annual/TTM Screener values cannot masquerade as quarterly values.**
  `screener_value_is_compatible()` rejects anything annual/TTM/trailing/FY, so a
  metric the PDF cannot supply resolves to *unavailable*, not to a mismatched
  Screener number.
- **Quarterly scoring is separate from annual scoring.**
  `scoring.assess_quarterly` never uses the 3-year-average rule; the annual
  `scoring.assess` is unchanged.
- **A PDF-reported ratio is a validation reference** when Python can
  independently calculate the ratio; it only becomes the value when Python
  cannot (e.g. a balance-sheet ratio, with no balance sheet in the results
  table).

## Provenance

Every metric carries `{value, period, source, formula, validation, unit, note}`,
with `source ∈ {pdf_raw, python_derived, pdf_reported_validation,
screener_fallback, unavailable}`. Exposed on the model as:

- `meta["raw_provenance"]` — each raw P&L line per quarter (`pdf_raw`, with the
  text/OCR origin and the identity-validation result);
- `meta["metrics"]` — each ratio per quarter with its full provenance;
- `meta["reported_ratios"]` — the filing's own ratio table (validation source);
- `meta["column_source"]`, `meta["validated"]`, `meta["warnings"]`.

## What is Python-derived vs. reported vs. unavailable (Reliance sample)

| Metric | Source | Notes |
|---|---|---|
| EBITDA / EBIT / Net margin | **python_derived** | from page-9 raw P&L |
| Interest Coverage Ratio | **python_derived**, `validation=pass` | EBIT ÷ Finance Costs = the PDF's Interest Service Coverage (5.13 / 4.67) — both quarters |
| Sales / PAT growth (QoQ) | **python_derived** | current vs previous quarter |
| Tax Payout %, Interest %/Dep % of sales | **python_derived** | from page-9 raw |
| Debt/Equity, Current Ratio, Debt/Assets, Debtor & Inventory turnover | **pdf_reported_validation** | printed on page 10; not computable from a P&L alone (no balance sheet) |
| ROE, ROCE, ROA, Cash Conversion Cycle, CFO/PAT, Fixed Asset Turnover | **unavailable** | no PDF input; Screener would be annual/TTM → rejected |

Formulas (all in Python, both periods where inputs exist):

```
EBITDA            = PBT + Finance Costs + Depreciation
EBIT              = PBT + Finance Costs
EBITDA/EBIT/Net margin = <numerator> / Revenue from Operations
Interest Coverage = EBIT / Finance Costs
Tax Payout %      = (Current Tax + Deferred Tax) / PBT
Sales/PAT growth  = current / previous - 1        (quarter-on-quarter)
```

## What still requires (and is denied) Screener

ROE, ROCE, ROA, Cash Conversion Cycle, CFO/PAT and Fixed Asset Turnover need a
balance sheet or cash-flow statement, which a results table does not contain.
`core/screener.py` exposes only **annual + TTM** fundamentals
(`fundamental_period = "Mar 2026 (annual) + TTM P&L"`), so the period guard
rejects them for a quarter and the metrics remain *unavailable*. They are never
back-filled with an annual number.

## Extraction (why it is hybrid)

The consolidated P&L table's current-quarter column is a **rasterised image** in
the sample filing — its numbers are not in the text layer. So:

1. find the consolidated results table deterministically (never standalone);
2. detect the two quarter columns from the header (labels preserved from the
   PDF, e.g. `Jun-2026` / `Mar-2026` — not hard-coded);
3. read text columns from the word layer; **OCR** the rasterised column
   (pytesseract + `tesseract-ocr` from `packages.txt` on deploy; a pure-pip
   `rapidocr` fallback locally);
4. **validate** every period against accounting identities
   (Sales + Other Income = Total Income; Total Income − Total Expenses = PBT;
   PBT − taxes = PAT), and drop a single OCR outlier rather than show it wrong;
5. only the two quarter columns are ever read — the audited FY column and the
   year-ago quarter never enter a quarterly series, chart, or score.

## Scoring & charts

- `assess_quarterly(model, sector)` scores the current quarter's level metrics
  against the same sector thresholds, uses quarter-on-quarter change as a
  direction nudge (annual growth bands are not applied to a single QoQ figure),
  drops the CFO earnings-quality step, and lists unavailable metrics as data
  gaps. `average_3y` is always `None`.
- `app.py` branches on `meta["periodicity"] == "quarterly"`, skips the
  annual-only common-size rebuild, and shows a banner naming the two quarters
  and the OCR/validation status.
- Charts read `model.series(...)` / `model.years` and `.dropna()` empty series,
  so a two-column model draws exactly the two quarters; balance-sheet charts
  simply do not render. No structural chart change was required.

## Files changed

- `core/quarterly_pdf.py` — completed ingestion + provenance/derivation layer.
- `core/scoring.py` — added `assess_quarterly` (annual `assess` unchanged).
- `app.py` — PDF upload + auto-detect + quarterly branch/banner.
- `requirements.txt` — pdfplumber, PyMuPDF, pytesseract, Pillow, rapidocr.
- `packages.txt` — `tesseract-ocr` (deploy OCR; from the branch scaffold).
- `tests/test_quarterly_pdf.py` — 35 tests (units, derivation, acceptance).
- `docs/quarterly_pdf_implementation_report.md`, `docs/quarterly-data-plan.md`.

## Remaining limitations

- OCR of the rasterised columns is validated by accounting identities (P&L) and,
  for interest coverage, cross-checked against the PDF's reported ratio; other
  page-10 reported ratios (D/E, current ratio, turnovers) for the current
  quarter are OCR-read decimals with no independent Python cross-check, so they
  carry an `ocr` provenance note.
- Tested end-to-end on one filing (Reliance). A materially different table
  layout may need pattern tuning; the loader fails loudly rather than guessing.
- Segment data and the balance sheet/cash-flow sections are out of scope for V1.
