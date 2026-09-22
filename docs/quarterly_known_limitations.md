# Quarterly extraction — known limitations

Recorded rather than worked around. Each entry says what fails, why, and what
the engine does instead. The rule throughout: an explicit, explained failure is
acceptable; an invented number is not.

Measured against `tests/benchmark/corpus.py` (7 real filings).

## 1. Scanned / image-only filings need an OCR engine that is not deployable

**Affects:** BEL, Jash Engineering (whole filing), Anand Rathi (consolidated
section only), Reliance (current-quarter column only).

The results table in these filings is a raster image. `pdfplumber` returns
either nothing or transliterated noise (BEL's text layer yields
`tfiRd THRSTTRHR` for a Hindi/English page).

`core/quarterly_pdf.py` supports OCR through two lazily-imported engines,
PaddleOCR PP-StructureV3 (primary) and Surya (secondary cross-check), and only
invokes them for columns that native extraction could not read. Neither is
listed in `requirements.txt`:

* `paddlepaddle` publishes no wheel for the Python version Streamlit Community
  Cloud builds on, and is in any case too large for that image's memory limit.
* `surya-ocr` is Torch-based and multi-GB.

`packages.txt` and `runtime.txt` are both empty, so there is no system-level
OCR on the deployed environment either.

**Current behaviour, by case:**

| Case | Behaviour |
|---|---|
| Whole filing is a scan (BEL, Jash) | `QuarterlyPDFError` with an explanation. Nothing is returned. |
| Consolidated is a scan, standalone is text (Anand Rathi) | Falls back to standalone, labels `scope="standalone"`, records `scope_fallback_reason`, and the UI shows a warning. |
| One column is a scan (Reliance) | That column's cells stay unavailable (`—`). `validation_detail` reports `insufficient_data` and overall confidence is forced to `low`. |

**To enable OCR**, install `paddlepaddle` + `paddleocr` (and optionally
`surya-ocr`) in an environment that can host them. The code path already
exists; only the dependency is missing. `q.ocr_available()` reports whether an
engine was found.

## 2. Itemised exceptional-item blocks are not summed

**Affects:** TCS.

Some filings do not print a single "Exceptional items" figure. TCS prints a
heading with no value, then itemises beneath it ("Re-structuring expenses",
"Statutory impact of new Labour Codes", "Settlement of legal claim"). The
engine captures no `Exceptional Items` value for such a filing rather than
picking one of the sub-lines.

This is safe — the PBT identity accepts the unadjusted form, and TCS validates —
but the exceptional amount itself is unavailable where a single line would have
given it. Summing an itemised block requires tracking the extent of the block,
which is not yet implemented.

## 3. Industry-specific expense lines are not individually modelled

**Affects:** BSE (exchange), and any bank/NBFC with a similar structure.

`_EXPENSE_COMPONENTS` models a manufacturer's expense breakdown. An exchange
reports lines with no equivalent there — "Technology expense", "Clearing and
settlement expense", "Regulatory contribution". These rows are not captured, so
the component-sum reconciliation in `_reconcile_expenses` cannot run for such a
filing.

The *totals* are read correctly and the accounting identities reconcile (BSE
validates on both quarters), so no figure is wrong. What is missing is the
finer-grained expense breakdown and the extra cross-check it would allow.

## 4. `meta["source_pages"]` reports a page range, not the pages actually used

`load_quarterly_pdf` records `[page_index + 1, page_index + 2]`, because the
reported-ratio table usually sits on the page after the P&L. When it does not,
the second page number is reported despite contributing nothing. For TCS this
means page 9 (the segment-information page) is listed although only page 8 was
used for the P&L.

Cosmetic — it affects a provenance caption, not any figure — but it should
report the pages actually read.

## 5. Only two quarters, and no year-ago quarter

By design. The engine reads the current and immediately preceding quarter and
excludes annual, TTM and cumulative (9M / half-year / YTD) columns entirely, so
a Screener annual or TTM figure can never be presented as a quarter. Filings do
print a year-ago quarter, and YoY could be derived from it, but it is not
currently extracted — so quarterly mode shows QoQ only.
