# Test fixtures

The quarterly-ingestion acceptance test (`tests/test_quarterly_pdf.py`) needs a
real consolidated quarterly-results PDF. The sample is **not committed** (it is a
multi-MB binary; see `.gitignore`).

To run the full acceptance test, provide the Reliance filing in any one of:

1. `tests/fixtures/reliance-quarterly-results.pdf`
2. the path in the `FUNDACHECK_QPDF` environment variable
3. `~/Downloads/reliance-quarterly-results.pdf`

If none is present, the end-to-end test is **skipped** (not failed); the
pure-function unit tests always run.
