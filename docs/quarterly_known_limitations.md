# Quarterly extraction — known limitations

Recorded rather than worked around. Each entry says what fails, why, and what
the engine does instead. The rule throughout: an explicit, explained failure is
acceptable; an invented number is not.

Measured against `tests/benchmark/corpus.py` (7 real filings).

## 1. Scanned / image-only filings need an OCR engine that does not fit the deploy

**Affects:** BEL, Jash Engineering (whole filing), Anand Rathi (consolidated
section only), Reliance (current-quarter column only).

The results table in these filings is a raster image. `pdfplumber` returns
either nothing or transliterated noise (BEL's text layer yields
`tfiRd THRSTTRHR` for a Hindi/English page).

OCR now lives behind a provider interface in `core/quarterly_ocr.py` (see
`docs/quarterly_ocr_deployment.md`), and is invoked only for columns that
native extraction could not read.

**Correction to an earlier version of this document:** it stated that
`runtime.txt` was empty and that PaddlePaddle had no wheel for the deployment's
Python. Both were wrong. `runtime.txt` pins `python-3.12`, and
`paddlepaddle-3.3.1-cp312-cp312-manylinux1_x86_64.whl` exists and installs
cleanly, as does `paddleocr` 3.7.0. The real obstacle is size, not
availability: about 1.2 GB once the text-recognition models download at first
use, and about 2.3 GB with PP-StructureV3 enabled.
The measurements are in `docs/quarterly_ocr_deployment.md`.

The OCR stack is therefore pinned in `requirements-ocr.txt` and deliberately
kept out of `requirements.txt`, because a failed Community Cloud build would
take down the annual/Excel workflow too. `requirements-ocr.txt` documents how
to enable it, and `RemoteOCRProvider` is the fallback if it does not fit.

**Current behaviour, by case:**

| Case | Behaviour |
|---|---|
| Whole filing is a scan (BEL, Jash) | `QuarterlyPDFError` with an explanation. Nothing is returned. |
| Consolidated is a scan, standalone is text (Anand Rathi) | Falls back to standalone, labels `scope="standalone"`, records `scope_fallback_reason`, and the UI shows a warning. |
| One column is a scan (Reliance) | That column's cells stay unavailable (`—`). `validation_detail` reports `insufficient_data` and overall confidence is forced to `low`. |

**To enable OCR**, see `requirements-ocr.txt` and
`docs/quarterly_ocr_deployment.md`. `q.ocr_available()` reports whether an
engine started, and `q.ocr_status()` / `meta["ocr_status"]` report which
provider and, when none, why.

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
