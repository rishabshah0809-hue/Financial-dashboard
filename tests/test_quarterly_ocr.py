"""
Tests for the OCR provider abstraction (core/quarterly_ocr.py).

These run WITHOUT any OCR engine installed -- that is the point. The contract
they pin down is the one the deployed app depends on: when no engine is
present the application must still start, OCR calls must return empty results
rather than raising, and the reason must be reportable to the user.

Engine-specific behaviour is covered by the benchmark against real filings,
not here.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))

from core import quarterly_ocr as qo                 # noqa: E402


class _Recorder(qo.QuarterlyOCRProvider):
    """Minimal provider used to check the interface's default behaviour."""

    name = "recorder"

    def __init__(self, tokens=()):
        self.calls = []
        self._tokens = tuple(tokens)

    def available(self) -> bool:
        return True

    def ocr_page(self, image):
        self.calls.append(image)
        return qo.OCRPageResult(tokens=self._tokens, engine=self.name)


# --------------------------------------------------------------------------
# data objects
# --------------------------------------------------------------------------
class TokenGeometry(unittest.TestCase):
    def test_centres(self):
        t = qo.OCRToken("1,234", (10.0, 20.0, 50.0, 40.0), 0.97)
        self.assertEqual(t.x_center, 30.0)
        self.assertEqual(t.y_center, 30.0)

    def test_low_confidence_threshold(self):
        self.assertTrue(qo.OCRToken("x", (0, 0, 1, 1), 0.10).is_low_confidence())
        self.assertFalse(qo.OCRToken("x", (0, 0, 1, 1), 0.99).is_low_confidence())

    def test_empty_result_is_falsey_and_has_no_confidence(self):
        empty = qo.OCRPageResult(error="nothing installed")
        self.assertFalse(empty)
        self.assertEqual(empty.mean_confidence, 0.0)

    def test_mean_confidence(self):
        res = qo.OCRPageResult(tokens=(
            qo.OCRToken("a", (0, 0, 1, 1), 0.8),
            qo.OCRToken("b", (0, 0, 1, 1), 1.0)))
        self.assertTrue(res)
        self.assertAlmostEqual(res.mean_confidence, 0.9)


class PdfSpaceMapping(unittest.TestCase):
    def test_maps_image_pixels_back_to_pdf_points(self):
        # y centre 100px at zoom 5 -> 20pt below the region's origin
        tokens = [qo.OCRToken("72,275", (0.0, 90.0, 40.0, 110.0), 0.99)]
        out = qo.tokens_to_pdf_space(tokens, origin_y=300.0, zoom=5.0)
        self.assertEqual(len(out), 1)
        y, text, conf = out[0]
        self.assertAlmostEqual(y, 320.0)
        self.assertEqual(text, "72,275")
        self.assertAlmostEqual(conf, 0.99)

    def test_sorted_top_to_bottom_and_blanks_dropped(self):
        tokens = [
            qo.OCRToken("second", (0, 200, 10, 220), 0.9),
            qo.OCRToken("   ", (0, 0, 10, 20), 0.9),
            qo.OCRToken("first", (0, 0, 10, 20), 0.9),
        ]
        out = qo.tokens_to_pdf_space(tokens, origin_y=0.0, zoom=1.0)
        self.assertEqual([t[1] for t in out], ["first", "second"])


# --------------------------------------------------------------------------
# interface defaults
# --------------------------------------------------------------------------
class InterfaceDefaults(unittest.TestCase):
    def test_ocr_pages_delegates_per_image(self):
        rec = _Recorder()
        results = rec.ocr_pages(["a", "b", "c"])
        self.assertEqual(len(results), 3)
        self.assertEqual(rec.calls, ["a", "b", "c"])

    def test_ocr_financial_table_falls_back_to_text(self):
        """A provider with no structure model must still return text."""
        rec = _Recorder(tokens=(qo.OCRToken("x", (0, 0, 1, 1), 0.9),))
        self.assertTrue(rec.ocr_financial_table("img"))


# --------------------------------------------------------------------------
# null provider -- the deployed no-engine case
# --------------------------------------------------------------------------
class NullProvider(unittest.TestCase):
    def test_never_raises_and_reports_reason(self):
        p = qo.NullOCRProvider("engine not installed")
        self.assertFalse(p.available())
        res = p.ocr_page(None)
        self.assertFalse(res)
        self.assertEqual(res.error, "engine not installed")
        # the batch and table entry points must be just as safe
        self.assertEqual(len(p.ocr_pages([None, None])), 2)
        self.assertFalse(p.ocr_financial_table(None))


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------
class ProviderSelection(unittest.TestCase):
    def setUp(self):
        qo.reset_provider()
        self.addCleanup(qo.reset_provider)

    def _env(self, **kw):
        return mock.patch.dict("os.environ", kw, clear=False)

    def test_disabled_by_configuration(self):
        with self._env(FUNDACHECK_OCR_PROVIDER="none"):
            qo.reset_provider()
            self.assertIsInstance(qo.get_provider(), qo.NullOCRProvider)
            self.assertFalse(qo.ocr_available())

    def test_remote_requested_but_unconfigured_degrades_to_null(self):
        with self._env(FUNDACHECK_OCR_PROVIDER="remote", FUNDACHECK_OCR_URL=""):
            qo.reset_provider()
            provider = qo.get_provider()
            self.assertIsInstance(provider, qo.NullOCRProvider)
            self.assertIn("FUNDACHECK_OCR_URL", provider.reason)

    def test_remote_selected_when_url_is_set(self):
        with self._env(FUNDACHECK_OCR_PROVIDER="remote",
                       FUNDACHECK_OCR_URL="https://example.invalid/ocr"):
            qo.reset_provider()
            provider = qo.get_provider()
            self.assertIsInstance(provider, qo.RemoteOCRProvider)
            self.assertTrue(provider.available())

    def test_auto_prefers_configured_remote(self):
        with self._env(FUNDACHECK_OCR_PROVIDER="auto",
                       FUNDACHECK_OCR_URL="https://example.invalid/ocr"):
            qo.reset_provider()
            self.assertIsInstance(qo.get_provider(), qo.RemoteOCRProvider)

    def test_status_always_reports_availability(self):
        with self._env(FUNDACHECK_OCR_PROVIDER="none"):
            qo.reset_provider()
            status = qo.provider_status()
            self.assertIn("provider", status)
            self.assertIn("available", status)
            self.assertFalse(status["available"])

    def test_provider_is_cached_until_reset(self):
        with self._env(FUNDACHECK_OCR_PROVIDER="none"):
            qo.reset_provider()
            self.assertIs(qo.get_provider(), qo.get_provider())


# --------------------------------------------------------------------------
# remote provider wire format
# --------------------------------------------------------------------------
class RemoteWireFormat(unittest.TestCase):
    def setUp(self):
        self.p = qo.RemoteOCRProvider(url="https://example.invalid/ocr")

    def test_unconfigured_url_is_an_error_not_an_exception(self):
        res = qo.RemoteOCRProvider(url="").ocr_page(b"x")
        self.assertFalse(res)
        self.assertIn("FUNDACHECK_OCR_URL", res.error)

    def test_parses_tokens_and_tables(self):
        payload = {
            "tokens": [{"text": "1,234", "bbox": [1, 2, 3, 4], "confidence": 0.9},
                       {"text": "   ", "bbox": [0, 0, 0, 0], "confidence": 1.0}],
            "tables": [{"rows": [["Revenue", "72,275"], ["PAT", "13,420"]]}],
        }
        res = self.p._parse_payload(payload)
        self.assertEqual(len(res.tokens), 1)        # blank token dropped
        self.assertEqual(res.tokens[0].text, "1,234")
        self.assertEqual(res.tokens[0].bbox, (1.0, 2.0, 3.0, 4.0))
        self.assertEqual(res.tables[0].rows[0], ("Revenue", "72,275"))

    def test_malformed_payload_is_reported_not_raised(self):
        self.assertIn("malformed", self.p._parse_payload(["not", "a", "dict"]).error)

    def test_garbage_fields_do_not_raise(self):
        res = self.p._parse_payload(
            {"tokens": [{"text": "x", "bbox": "nonsense", "confidence": "high"}]})
        self.assertEqual(res.tokens[0].bbox, (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(res.tokens[0].confidence, 0.0)

    def test_network_failure_is_reported_not_raised(self):
        with mock.patch("requests.post", side_effect=OSError("no route")):
            res = self.p.ocr_page(b"\x89PNG")
            self.assertFalse(res)
            self.assertIn("no route", res.error)


# --------------------------------------------------------------------------
# local provider parsing (no engine needed)
# --------------------------------------------------------------------------
class LocalParsing(unittest.TestCase):
    def setUp(self):
        self.p = qo.LocalPaddleOCRProvider()

    def test_parses_paddleocr_3x_results(self):
        raw = [{"res": {"rec_texts": ["Revenue from operations", "72,275"],
                        "rec_scores": [0.99, 0.97],
                        "rec_polys": [[[0, 0], [100, 0], [100, 20], [0, 20]],
                                      [[0, 30], [100, 30], [100, 50], [0, 50]]]}}]
        tokens = self.p._parse_3x(raw)
        self.assertEqual([t.text for t in tokens],
                         ["Revenue from operations", "72,275"])
        self.assertEqual(tokens[1].bbox, (0.0, 30.0, 100.0, 50.0))
        self.assertAlmostEqual(tokens[1].confidence, 0.97)

    def test_parses_paddleocr_2x_results(self):
        raw = [[[[[0, 0], [10, 0], [10, 5], [0, 5]], ("13,420", 0.95)]]]
        tokens = self.p._parse_2x(raw)
        self.assertEqual(tokens[0].text, "13,420")
        self.assertAlmostEqual(tokens[0].confidence, 0.95)

    def test_malformed_engine_output_is_skipped_not_raised(self):
        self.assertEqual(self.p._parse_3x([{"res": {}}]), [])
        self.assertEqual(self.p._parse_2x([[None, "junk"]]), [])
        self.assertEqual(self.p._parse_3x(None), [])

    def test_table_html_to_rows(self):
        html = ("<table><tr><td>Revenue</td><td>72,275</td></tr>"
                "<tr><td>PAT</td><td>13,420</td></tr></table>")
        rows = self.p._rows_from_html(html)
        self.assertEqual(rows, (("Revenue", "72,275"), ("PAT", "13,420")))

    def test_empty_table_html(self):
        self.assertEqual(self.p._rows_from_html(""), ())

    def test_bbox_from_both_poly_shapes(self):
        self.assertEqual(
            self.p._poly_to_bbox([[0, 0], [10, 0], [10, 5], [0, 5]]),
            (0.0, 0.0, 10.0, 5.0))
        self.assertEqual(self.p._poly_to_bbox([1, 2, 3, 4]), (1.0, 2.0, 3.0, 4.0))
        self.assertEqual(self.p._poly_to_bbox("junk"), (0.0, 0.0, 0.0, 0.0))

    def test_reports_absence_rather_than_raising(self):
        """With no paddleocr installed, this must be a status, not a crash."""
        if self.p.available():
            self.skipTest("paddleocr IS installed in this environment")
        res = self.p.ocr_page(b"\x89PNG")
        self.assertFalse(res)
        self.assertTrue(res.error)
        self.assertIn("paddleocr", self.p.status()["error"])


# --------------------------------------------------------------------------
# the parser's view of OCR
# --------------------------------------------------------------------------
class ParserIntegration(unittest.TestCase):
    """core/quarterly_pdf must never import an engine directly."""

    def test_quarterly_pdf_has_no_engine_imports(self):
        source = (Path(__file__).parents[1] / "core" / "quarterly_pdf.py").read_text(
            encoding="utf-8")
        for banned in ("import paddleocr", "from paddleocr",
                       "import surya", "from surya"):
            self.assertNotIn(banned, source,
                             f"{banned!r} must live in core/quarterly_ocr.py only")

    def test_ocr_helpers_are_safe_without_an_engine(self):
        from core import quarterly_pdf as q
        self.assertIsInstance(q.ocr_available(), bool)
        status = q.ocr_status()
        self.assertIn("provider", status)
        self.assertIn("available", status)


if __name__ == "__main__":                           # pragma: no cover
    unittest.main(verbosity=2)
