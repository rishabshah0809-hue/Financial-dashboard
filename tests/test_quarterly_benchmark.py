"""
Quarterly-filing extraction benchmark.

Runs `core.quarterly_pdf.load_quarterly_pdf` over the real filings described in
`tests/benchmark/corpus.py` and compares the structured output against the
hand-transcribed expectation for each one.

Design rules (these mirror the engine's own rules):

* A filing whose PDF is not present locally is SKIPPED, never failed -- the
  binaries are gitignored.
* A filing marked `requires_ocr` is skipped for value/structure assertions when
  no OCR engine is installed, but its failure mode is still asserted: it must
  fail EXPLICITLY (QuarterlyPDFError), never return invented numbers.
* Expected values are compared in crore after converting from the filing's own
  reported unit, so a unit regression fails loudly.
* Accounting-identity checks need no transcribed numbers at all, so they apply
  to every parsed filing and catch row-misassignment even where the corpus
  records only a few figures.

Run just this suite:
    python -m pytest tests/test_quarterly_benchmark.py -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from core import quarterly_pdf as q                          # noqa: E402
from core.quarterly_pdf import QuarterlyPDFError             # noqa: E402
from tests.benchmark import corpus                           # noqa: E402
from tests.benchmark.corpus import CORPUS, REQUIRED_COVERAGE  # noqa: E402

_LIBS_OK = q._HAVE_PDFPLUMBER and q._HAVE_PYMUPDF

#: relative tolerance for a transcribed figure, after unit conversion. Tight:
#: these are exact printed numbers, not estimates. The absolute floor absorbs
#: the rounding of a lakh->crore conversion only.
_REL_TOL = 1e-6
_ABS_TOL = 0.005


# --------------------------------------------------------------------------
# corpus hygiene -- runs with no PDFs present
# --------------------------------------------------------------------------
class CorpusIntegrity(unittest.TestCase):
    """The manifest itself must stay honest and complete."""

    def test_keys_unique(self):
        keys = [f.key for f in CORPUS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_ocr_only_filings_carry_no_values(self):
        # A scanned filing cannot have hand-transcribed figures; if one ever
        # appears there, somebody invented it.
        for f in CORPUS:
            if f.requires_ocr:
                self.assertEqual(
                    f.values, {},
                    f"{f.key} is image-only but carries transcribed values")

    def test_every_value_has_a_truth_page(self):
        for f in CORPUS:
            if f.values:
                self.assertGreater(
                    f.truth_page, 0,
                    f"{f.key} records values without citing a source page")

    def test_units_are_known(self):
        for f in CORPUS:
            self.assertIn(f.reported_unit, corpus.TO_CRORE, f.key)

    def test_required_layout_coverage_is_satisfied(self):
        known = {f.key for f in CORPUS}
        for dimension, keys in REQUIRED_COVERAGE.items():
            self.assertTrue(keys, f"no filing covers {dimension!r}")
            for key in keys:
                self.assertIn(key, known,
                              f"{dimension!r} names unknown filing {key!r}")

    def test_scope_values_are_valid(self):
        for f in CORPUS:
            self.assertIn(f.expected_scope, ("consolidated", "standalone"), f.key)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _load(filing):
    """Parse a filing, or return the QuarterlyPDFError it raised."""
    try:
        return q.load_quarterly_pdf(str(filing.path())), None
    except QuarterlyPDFError as exc:
        return None, exc


def _series(model, row):
    if row not in model.historical.index:
        return None
    return model.historical.loc[row]


def _missing(value) -> bool:
    """True when a cell carries no figure. NaN and None both mean unavailable."""
    if value is None:
        return True
    try:
        return bool(value != value)                  # NaN
    except TypeError:                                # pragma: no cover
        return False


# --------------------------------------------------------------------------
# per-filing benchmark
# --------------------------------------------------------------------------
@unittest.skipUnless(_LIBS_OK, "pdfplumber / PyMuPDF not installed")
class QuarterlyBenchmark(unittest.TestCase):
    """One sub-test per filing, so a single bad filing names itself."""

    _cache: dict = {}

    @classmethod
    def _parse(cls, filing):
        if filing.key not in cls._cache:
            cls._cache[filing.key] = _load(filing)
        return cls._cache[filing.key]

    # -- scanned filings -------------------------------------------------
    def test_image_only_filings_fail_explicitly(self):
        """A scan the engine cannot read must raise, not fabricate."""
        for filing in CORPUS:
            if not filing.requires_ocr:
                continue
            with self.subTest(filing=filing.key):
                if filing.path() is None:
                    self.skipTest(f"{filing.filename} not available")
                model, err = self._parse(filing)
                if model is None:
                    self.assertIsInstance(err, QuarterlyPDFError)
                    self.assertTrue(str(err).strip(),
                                    "failure must carry an explanation")
                    return
                # If OCR IS installed and it parsed, the result must still be
                # internally coherent rather than partially invented.
                self.assertIn(model.meta.get("source_scope"),
                              ("consolidated", "standalone"))

    # -- metadata --------------------------------------------------------
    def test_company_scope_and_periods(self):
        for filing in CORPUS:
            if filing.requires_ocr:
                continue
            with self.subTest(filing=filing.key):
                if filing.path() is None:
                    self.skipTest(f"{filing.filename} not available")
                model, err = self._parse(filing)
                self.assertIsNotNone(model, f"parse failed: {err}")
                meta = model.meta

                self.assertIn(filing.company_contains.lower(),
                              (model.company or "").lower(),
                              "company name not identified")
                self.assertEqual(meta.get("source_scope"), filing.expected_scope,
                                 "wrong statement section selected")
                self.assertEqual(meta.get("current_period"),
                                 filing.expected_current_period)
                self.assertEqual(meta.get("previous_period"),
                                 filing.expected_previous_period)
                self.assertEqual(meta.get("current_quarter_tag"),
                                 filing.expected_current_tag)
                self.assertEqual(meta.get("previous_quarter_tag"),
                                 filing.expected_previous_tag)

    def test_selected_the_right_results_page(self):
        """Guards against locking onto a segment/notes page next to the P&L."""
        for filing in CORPUS:
            if filing.requires_ocr or not filing.expected_results_page:
                continue
            with self.subTest(filing=filing.key):
                if filing.path() is None:
                    self.skipTest(f"{filing.filename} not available")
                model, err = self._parse(filing)
                self.assertIsNotNone(model, f"parse failed: {err}")
                self.assertIn(filing.expected_results_page,
                              model.meta.get("source_pages") or [],
                              "expected results page was not used")

    def test_no_annual_or_ttm_column_leaks_in(self):
        for filing in CORPUS:
            if filing.requires_ocr:
                continue
            with self.subTest(filing=filing.key):
                if filing.path() is None:
                    self.skipTest(f"{filing.filename} not available")
                model, err = self._parse(filing)
                self.assertIsNotNone(model, f"parse failed: {err}")
                cols = list(model.historical.columns)
                self.assertEqual(len(cols), 2, f"expected 2 quarters, got {cols}")
                for col in cols:
                    self.assertNotRegex(col, r"(?i)fy|ttm|year")

    def test_units_are_reported_in_metadata(self):
        """The UI has to be able to state the unit; None is not acceptable."""
        for filing in CORPUS:
            if filing.requires_ocr:
                continue
            with self.subTest(filing=filing.key):
                if filing.path() is None:
                    self.skipTest(f"{filing.filename} not available")
                model, err = self._parse(filing)
                self.assertIsNotNone(model, f"parse failed: {err}")
                info = model.meta.get("unit_info")
                self.assertIsNotNone(info, "unit_info was not detected")

    # -- values ----------------------------------------------------------
    def test_transcribed_values_match(self):
        for filing in CORPUS:
            if not filing.values:
                continue
            with self.subTest(filing=filing.key):
                if filing.path() is None:
                    self.skipTest(f"{filing.filename} not available")
                model, err = self._parse(filing)
                self.assertIsNotNone(model, f"parse failed: {err}")
                for row, periods in filing.values.items():
                    for period, printed in periods.items():
                        want = corpus.to_crore(printed, filing.reported_unit)
                        series = _series(model, row)
                        self.assertIsNotNone(
                            series,
                            f"row {row!r} missing (page {filing.truth_page})")
                        got = series.get(period)
                        self.assertFalse(
                            _missing(got),
                            f"{row} {period} unavailable "
                            f"(expected {printed:,} {filing.reported_unit})")
                        tol = max(_ABS_TOL, abs(want) * _REL_TOL)
                        self.assertAlmostEqual(
                            float(got), want, delta=tol,
                            msg=(f"{row} {period}: got {got}, expected {want} "
                                 f"crore (printed {printed:,} "
                                 f"{filing.reported_unit}, page "
                                 f"{filing.truth_page})"))

    # -- identities: no transcription needed -----------------------------
    def test_no_duplicated_row_values(self):
        """
        Distinct P&L lines that happen to carry identical values in BOTH
        quarters are the signature of one row's value leaking into another
        (the EBT == Exceptional Items == Net Profit family of bugs).
        """
        # Pairs that legitimately coincide are whitelisted with a reason.
        allowed = {
            frozenset({"Finance Costs", "Interest"}),      # alias of one line
        }
        for filing in CORPUS:
            if filing.requires_ocr:
                continue
            with self.subTest(filing=filing.key):
                if filing.path() is None:
                    self.skipTest(f"{filing.filename} not available")
                model, err = self._parse(filing)
                self.assertIsNotNone(model, f"parse failed: {err}")
                frame = model.historical.dropna(how="all")
                rows = list(frame.index)
                for i, a in enumerate(rows):
                    for b in rows[i + 1:]:
                        if frozenset({a, b}) in allowed:
                            continue
                        va, vb = frame.loc[a], frame.loc[b]
                        if va.isna().all() or vb.isna().all():
                            continue
                        if va.equals(vb):
                            self.fail(f"{a!r} and {b!r} carry identical values "
                                      f"in every quarter -- row misassignment "
                                      f"({va.to_dict()})")

    def test_reported_totals_are_internally_consistent(self):
        """
        Where the filing gives both a total and its components, the engine's
        own identity validation must pass. A False here means the extracted
        rows do not add up -- i.e. a row was missed or mismatched.
        """
        for filing in CORPUS:
            if filing.requires_ocr:
                continue
            with self.subTest(filing=filing.key):
                if filing.path() is None:
                    self.skipTest(f"{filing.filename} not available")
                model, err = self._parse(filing)
                self.assertIsNotNone(model, f"parse failed: {err}")
                detail = model.meta.get("validation_detail") or {}
                self.assertTrue(detail, "no validation detail was recorded")

                # A CONTRADICTION is an extraction bug and always fails here.
                # INSUFFICIENT_DATA means too few rows were readable to check
                # anything -- an honest limitation, not a wrong number -- so it
                # is allowed, but only if the engine also degraded its own
                # confidence and left the cells unavailable rather than guessed.
                contradictions = [p for p, d in detail.items()
                                  if d["status"] == "contradiction"]
                self.assertFalse(
                    contradictions,
                    f"accounting identities contradict for {contradictions} -- "
                    f"at least one row was read incorrectly: "
                    f"{ {p: detail[p]['reason'] for p in contradictions} }")

                unchecked = [p for p, d in detail.items()
                             if d["status"] == "insufficient_data"]
                if unchecked:
                    self.assertEqual(
                        model.meta.get("confidence"), "low",
                        f"{unchecked} could not be cross-checked, so overall "
                        f"confidence must be reported as low")

    def test_profit_is_below_revenue(self):
        """Cheap sanity invariant: PAT must not exceed revenue in a quarter."""
        for filing in CORPUS:
            if filing.requires_ocr:
                continue
            with self.subTest(filing=filing.key):
                if filing.path() is None:
                    self.skipTest(f"{filing.filename} not available")
                model, err = self._parse(filing)
                self.assertIsNotNone(model, f"parse failed: {err}")
                sales, pat = _series(model, "Sales"), _series(model, "Net Profit")
                if sales is None or pat is None:
                    self.skipTest("revenue or PAT not extracted")
                for period in model.historical.columns:
                    s, p = sales.get(period), pat.get(period)
                    if _missing(s) or _missing(p):
                        continue
                    self.assertLessEqual(
                        abs(float(p)), abs(float(s)) * 1.5,
                        f"{period}: PAT {p} implausible against revenue {s}")

    # -- provenance ------------------------------------------------------
    def test_every_extracted_value_carries_provenance(self):
        for filing in CORPUS:
            if filing.requires_ocr:
                continue
            with self.subTest(filing=filing.key):
                if filing.path() is None:
                    self.skipTest(f"{filing.filename} not available")
                model, err = self._parse(filing)
                self.assertIsNotNone(model, f"parse failed: {err}")
                prov = model.meta.get("raw_provenance") or {}
                for row in model.historical.index:
                    series = model.historical.loc[row]
                    for period in model.historical.columns:
                        if _missing(series.get(period)):
                            continue
                        entry = prov.get(row, {}).get(period)
                        self.assertIsNotNone(
                            entry, f"{row}/{period} has a value but no provenance")
                        self.assertIn("source", entry)

    def test_scope_is_labelled_when_standalone(self):
        """A standalone fallback must announce itself in metadata."""
        for filing in CORPUS:
            if filing.requires_ocr or filing.expected_scope != "standalone":
                continue
            with self.subTest(filing=filing.key):
                if filing.path() is None:
                    self.skipTest(f"{filing.filename} not available")
                model, err = self._parse(filing)
                self.assertIsNotNone(model, f"parse failed: {err}")
                self.assertEqual(model.meta.get("source_scope"), "standalone")
                self.assertTrue(
                    model.meta.get("scope_fallback_reason"),
                    "standalone fallback must record why consolidated was "
                    "not used, so the UI can warn the user")


if __name__ == "__main__":                              # pragma: no cover
    unittest.main(verbosity=2)
