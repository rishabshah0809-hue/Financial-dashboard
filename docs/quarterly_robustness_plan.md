# Quarterly robustness — implementation plan (company-agnostic)

Goal: make the quarterly pipeline company-agnostic and production-ready without
breaking the existing architecture or the annual/Excel path. Verified against
TWO real filings with different layouts: Reliance (image+text, ₹ crore, integer,
"Particulars", day-first dates) and Lemon Tree Hotels (pure text, ₹ Lakhs,
2-decimal, no "Particulars", month-first dates).

## Concrete gaps found (from the two real PDFs)
1. `_find_results_page` hard-required the word "Particulars" → Lemon Tree has no
   such column → whole parse failed. Must classify pages semantically.
2. Date regex assumed day-first ("30th Jun'26"); Lemon Tree is month-first
   ("June 30, 2026") → day misread as year. Need an order-agnostic date parser.
3. Units are per-document ("₹ in Lakhs" vs "₹ crore") → must be detected and
   normalized; never assume crore.
4. Decimal convention is per-document (Reliance integer crore; Lemon Tree
   2-decimal lakhs, sometimes with the separator dropped in the text layer).
   Detect it; identities hold as integers so ratios are scale-safe.
5. Row labels differ (Lemon Tree has no GST line, a combined EBITDA-ish line,
   different PAT wording). Need a real synonym layer, incl. bank/NBFC lines.

## Approach (staged, independently testable)
New pure module `core/quarterly_semantics.py` (no PDF deps) holding the
document-agnostic logic, unit-tested on synthetic inputs:
- `parse_date_token` — order-agnostic (day-first, month-first, dotted numeric).
- `classify_period_type` — quarter | annual | ttm | cumulative(9M/half/YTD).
- `detect_unit` / `to_crore` — lakh/crore/million/thousand → crore + metadata.
- `detect_decimals` / `parse_amount` — per-document decimal convention.
- `ROW_SYNONYMS` + `match_row_label` — semantic row mapping (incl. banks).
- `RATIO_REGISTRY` — one definition per ratio {formula, inputs, direction}.

`core/quarterly_pdf.py` becomes the orchestrator over clear stages:
document → page classification → section (consolidated, not standalone) →
periods (+type, exclude annual/ttm/cumulative) → units → raw extraction
(text + targeted OCR) → accounting validation → Python derivation
(RATIO_REGISTRY) → PDF-reported validation → guarded Screener → availability +
confidence.

`core/scoring.py` — quarterly scoring already separate; keep, make it read the
registry's direction and expose per-metric reason (transparent).

`app.py` — data-quality panel + provenance/methodology expander + header
(company / current vs previous / consolidated / confidence); missing shown as
"—", never 0. Annual/Excel path untouched.

## Non-negotiables
- Annual/Excel mode unchanged. Quarterly isolated behind meta.periodicity.
- LLM never computes a number. Screener annual/TTM never becomes quarterly.
- No fabrication: unavailable stays "—".

## Testing
Real: Reliance + Lemon Tree acceptance. Synthetic (fpdf2, text PDFs): unit
variants, column ordering, Q vs date naming, annual+quarterly mixed (exclude
annual), standalone-only (reject), missing ratio table, bank-style, no-GST.
Plus pure-unit tests for every semantics function.
