# FundaCheck — Quarterly PDF ingestion plan

## Goal

Allow FundaCheck to accept a listed-company quarterly results PDF instead of requiring a Screener-style annual Excel workbook.

Phase 1 intentionally uses **only the latest quarter and immediately preceding quarter** from the company's **unaudited consolidated financial results** section. The uploaded PDF remains the primary source; Screener is a fallback/enrichment source only when the PDF does not contain an input needed by the existing analysis.

## Source-of-truth rules

1. **Primary:** uploaded quarterly results PDF.
2. **Scope:** consolidated results only in phase 1.
3. **Periods:** detect the latest quarter and previous quarter from the table header; never assume column position alone.
4. **No annual/prior-year columns in the dashboard's quarterly series.** They may be read internally for validation, but must not be plotted or scored as quarterly periods.
5. **Screener fallback:** fetch only missing inputs. Every enriched value must carry its source and period so an annual/TTM value is never displayed as if it were a quarterly value.
6. **No invented values:** missing data remains unavailable. The LLM must never fill a financial number by guessing.
7. **Arithmetic stays in Python:** extraction may use OCR/vision, but ratios, growth, scores and chart series are calculated deterministically.

## Why the PDF needs a dedicated ingestion layer

The sample Reliance quarterly PDF demonstrates an important edge case. The visible consolidated table on page 9 has four columns (June 2026, March 2026, June 2025 and FY2026), but the machine-readable text layer does not reliably expose the first/current-quarter numeric column. The rendered table does contain it.

Therefore the parser should use a two-stage extraction strategy:

- deterministic PDF text/table extraction first;
- targeted OCR/vision fallback for cells that are missing from the text layer;
- validation against accounting identities before accepting extracted numbers.

The parser must fail loudly if it cannot establish the current-quarter and previous-quarter columns with sufficient confidence.

## Canonical quarterly model

The existing `FinancialModel` contract should remain the downstream interface wherever possible. Quarterly ingestion should produce the same metric names used by `core.derive`, `core.scoring`, charts and interpretation, but with exactly two periods, for example:

- `Q1 FY27 / Jun-26`
- `Q4 FY26 / Mar-26`

The model should also carry metadata such as:

- `periodicity = "quarterly"`
- `current_period`
- `previous_period`
- `source_type = "quarterly_pdf"`
- `source_pages`
- `source_company`
- `source_symbol`
- per-metric source/provenance where enrichment occurred

## Ratio/scoring changes

The annual scoring rule currently uses a latest value plus a 3-year average. That rule must **not** be silently reused for two-quarter data.

For quarterly mode:

- latest = current quarter
- previous = immediately preceding quarter
- trend = current vs previous quarter
- no fake 3-year average
- score calculations remain deterministic against the same sector bands
- the UI must explicitly say `Quarterly mode · 2 periods` rather than implying annual history

Where a ratio cannot be calculated from the two-quarter model, use the Screener fallback only when the available period is compatible; otherwise mark it unavailable.

## Dashboard behavior

All time-series charts in quarterly mode must use exactly two points. Do not manufacture six/ten-year history by mixing annual Screener data into the quarterly chart.

Charts that require unavailable balance-sheet/cash-flow inputs should show a clear data-gap state rather than a zero.

Segment data is out of scope for the first implementation unless it is already available in the targeted consolidated-results section. It can be added as a later PDF section parser.

## Sample PDF validation targets

For the supplied Reliance Industries Limited PDF, the parser should identify:

- company: Reliance Industries Limited
- symbol: RELIANCE
- current period: 30 June 2026
- previous quarter: 31 March 2026
- consolidated statement section beginning on the page headed `UNAUDITED CONSOLIDATED FINANCIAL RESULTS FOR THE QUARTER ENDED 30TH JUNE, 2026`
- reported quarterly ratios on the following ratio table

The first acceptance test is not visual similarity. It is numerical provenance: every value shown in the dashboard must be traceable to either the uploaded PDF or an explicitly labelled Screener fallback.
