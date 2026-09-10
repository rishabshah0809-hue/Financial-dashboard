"""
test_quarterly_pdf.py
---------------------
Tests for the quarterly-results PDF ingestion and its provenance-first,
Python-first derivation layer.

Three layers:
  1. Pure-function units (numbers, labels, periods, the Screener guard,
     formula correctness, division-by-zero) — always run.
  2. derive_quarterly() behaviour on synthetic raw values — always run.
  3. End-to-end acceptance against the supplied Reliance filing — skipped
     (never failed) when the sample PDF or the PDF/OCR libraries are absent.
"""

from __future__ import annotations

import os
import unittest
from datetime import date
from pathlib import Path

from core import quarterly_pdf as q
from core.parser import FinancialModel, load_model
from core.scoring import assess_quarterly
from core.sectors import get_sector


def _mk_period(label, end, kind="quarter", center=100.0, source="text"):
    return q._Period(label, end, kind, center - 20, center + 20, center, source)


# --------------------------------------------------------------------------
# 1. pure-function units
# --------------------------------------------------------------------------
class NumberNormalisation(unittest.TestCase):
    def test_integer_separators_and_noise(self):
        self.assertEqual(q._num_int("340,257"), 340257)
        self.assertEqual(q._num_int("340.257"), 340257)
        self.assertEqual(q._num_int("23:001"), 23001)
        self.assertEqual(q._num_int("287770"), 287770)

    def test_integer_parentheses_negative(self):
        self.assertEqual(q._num_int("(1,326)"), -1326)
        self.assertEqual(q._num_int("(1.326)"), -1326)

    def test_integer_blanks(self):
        for blank in ("", "-", "--", ".", None):
            self.assertIsNone(q._num_int(blank))

    def test_decimal_parsing(self):
        self.assertAlmostEqual(q._num_dec("12.54"), 12.54)
        self.assertAlmostEqual(q._num_dec("0.41"), 0.41)
        self.assertAlmostEqual(q._num_dec("(0.30)"), -0.30)
        self.assertEqual(q._num_dec("1,234"), 1234)


class LabelMatching(unittest.TestCase):
    def test_core_pnl_lines(self):
        self.assertEqual(q._match_line("Revenue from Operations"), "Sales")
        self.assertEqual(q._match_line("Total Income"), "Total Income")
        self.assertEqual(q._match_line("Finance Costs"), "Finance Costs")
        self.assertEqual(q._match_line("Profit Before Tax"), "Earnings Before Tax")

    def test_mangled_source_text_still_matches(self):
        self.assertEqual(q._match_line("Excise Duly"), "Excise Duty")
        self.assertEqual(q._match_line("Tax Expenses CurrentTax"), "Current Tax")
        self.assertEqual(q._match_line("Olher Income"), "Other Income")

    def test_net_profit_wins_over_share_line(self):
        self.assertEqual(
            q._match_line("Profit After Tax and Share of Profit (Loss) of "
                          "Associates and Joint Ventures"), "Net Profit")
        self.assertEqual(
            q._match_line("Share of Profit / (Loss) of Associates and Joint "
                          "Ventures"), "Share of Profit of Associates")

    def test_reported_ratio_labels(self):
        self.assertEqual(q._match_ratio("c) Debt Equity Ratio"), "Debt to Equity Ratio")
        self.assertEqual(q._match_ratio("d) Current Ratio"), "Current Ratio")
        self.assertEqual(q._match_ratio("b) Interest Service Coverage Ratio"),
                         "Interest Coverage Ratio")


class PeriodParsing(unittest.TestCase):
    def test_label_format_is_mon_yyyy(self):
        d = q._parse_date("30", "Jun", "26")
        self.assertEqual((d.year, d.month), (2026, 6))
        self.assertEqual(q._period_label(d), "Jun-2026")
        self.assertEqual(q._fy_quarter_tag(d), "Q1 FY27")

    def test_march_is_q4(self):
        d = q._parse_date("31", "Mar", "26")
        self.assertEqual(q._period_label(d), "Mar-2026")
        self.assertEqual(q._fy_quarter_tag(d), "Q4 FY26")

    def test_is_pdf(self):
        self.assertTrue(q.is_pdf("results.pdf"))
        self.assertTrue(q.is_pdf(None, b"%PDF-1.7\n..."))
        self.assertFalse(q.is_pdf("model.xlsx"))
        self.assertFalse(q.is_pdf(None, b"PK\x03\x04"))

    def test_alias(self):
        self.assertIs(q.load_quarterly_model, q.load_quarterly_pdf)


class SafeArithmetic(unittest.TestCase):
    def test_safe_div_zero_and_none(self):
        self.assertIsNone(q._safe_div(10, 0))
        self.assertIsNone(q._safe_div(None, 5))
        self.assertIsNone(q._safe_div(5, None))
        self.assertEqual(q._safe_div(10, 4), 2.5)


class ScreenerGuard(unittest.TestCase):
    def test_annual_ttm_rejected(self):
        for fp in ("Mar 2026 (annual) + TTM P&L", "FY2026", "TTM",
                   "trailing twelve months", "Year ended Mar-2026"):
            ok, why = q.screener_value_is_compatible(fp, "Jun-2026")
            self.assertFalse(ok, f"{fp!r} should be rejected")
            self.assertIn("Jun-2026", why)

    def test_missing_period_rejected(self):
        ok, _ = q.screener_value_is_compatible(None, "Jun-2026")
        self.assertFalse(ok)

    def test_fallback_rejects_annual_company(self):
        class FakeSC:
            fundamental_period = "Mar 2026 (annual) + TTM P&L"
        prov = q._screener_fallback("Return on Equity (ROE) %", "Jun-2026",
                                    "ACME", lambda name: FakeSC())
        self.assertEqual(prov.source, q.SOURCE_UNAVAILABLE)
        self.assertIn("rejected", prov.note.lower())


# --------------------------------------------------------------------------
# 2. derive_quarterly() on synthetic raw values (formula correctness)
# --------------------------------------------------------------------------
class DeriveQuarterly(unittest.TestCase):
    def setUp(self):
        self.prev = _mk_period("Q1", date(2026, 3, 31))
        self.cur = _mk_period("Q2", date(2026, 6, 30))
        # Q2: Sales 1000, PBT 200, Finance 50, Dep 100, Net 150, taxes 30+20
        self.raw = {
            "Sales": {"Q1": 800, "Q2": 1000},
            "Earnings Before Tax": {"Q1": 160, "Q2": 200},
            "Finance Costs": {"Q1": 40, "Q2": 50},
            "Depreciation": {"Q1": 80, "Q2": 100},
            "Net Profit": {"Q1": 120, "Q2": 150},
            "Current Tax": {"Q1": 25, "Q2": 30},
            "Deferred Tax": {"Q1": 15, "Q2": 20},
        }

    def test_python_formulas(self):
        frame, prov = q.derive_quarterly(self.raw, {}, [self.prev, self.cur], "ACME")
        # EBITDA margin Q2 = (200+50+100)/1000 = 0.35
        self.assertAlmostEqual(frame.loc["EBITDA Margin", "Q2"], 0.35)
        # EBIT margin = (200+50)/1000 = 0.25
        self.assertAlmostEqual(frame.loc["EBIT Margin", "Q2"], 0.25)
        # Net margin = 150/1000 = 0.15
        self.assertAlmostEqual(frame.loc["Net Profit Margin", "Q2"], 0.15)
        # Interest coverage = 250/50 = 5
        self.assertAlmostEqual(frame.loc["Interest Coverage Ratio", "Q2"], 5.0)
        # QoQ growth: sales 1000/800-1 = 0.25 ; PAT 150/120-1 = 0.25
        self.assertAlmostEqual(frame.loc["Sales Growth", "Q2"], 0.25)
        self.assertAlmostEqual(frame.loc["Net Profit Growth", "Q2"], 0.25)
        self.assertEqual(prov["EBITDA Margin"]["Q2"]["source"], q.SOURCE_PY)
        # provenance value is a percentage for margins
        self.assertAlmostEqual(prov["EBITDA Margin"]["Q2"]["value"], 35.0)

    def test_division_by_zero_growth_unavailable(self):
        raw = dict(self.raw)
        raw["Sales"] = {"Q1": 0, "Q2": 1000}
        _frame, prov = q.derive_quarterly(raw, {}, [self.prev, self.cur], "ACME")
        self.assertEqual(prov["Sales Growth"]["Q2"]["source"], q.SOURCE_UNAVAILABLE)
        self.assertIsNone(prov["Sales Growth"]["Q2"]["value"])

    def test_pdf_reported_validation_pass_and_mismatch(self):
        # reported interest coverage matches python (5.0) -> pass
        rep_pass = {"Interest Coverage Ratio": {"Q2": 5.0}}
        _f, prov = q.derive_quarterly(self.raw, rep_pass, [self.prev, self.cur], "ACME")
        self.assertEqual(prov["Interest Coverage Ratio"]["Q2"]["validation"], "pass")
        # reported wildly different -> mismatch (flagged, python kept)
        rep_bad = {"Interest Coverage Ratio": {"Q2": 9.99}}
        _f2, prov2 = q.derive_quarterly(self.raw, rep_bad, [self.prev, self.cur], "ACME")
        self.assertEqual(prov2["Interest Coverage Ratio"]["Q2"]["validation"], "mismatch")
        self.assertEqual(prov2["Interest Coverage Ratio"]["Q2"]["source"], q.SOURCE_PY)

    def test_reported_only_ratio_is_pdf_reported(self):
        rep = {"Debt to Equity Ratio": {"Q2": 0.40, "Q1": 0.41}}
        frame, prov = q.derive_quarterly(self.raw, rep, [self.prev, self.cur], "ACME")
        self.assertEqual(prov["Debt to Equity Ratio"]["Q2"]["source"],
                         q.SOURCE_PDF_REPORTED)
        self.assertAlmostEqual(frame.loc["Debt to Equity Ratio", "Q2"], 0.40)

    def test_screener_only_when_python_cannot_calculate(self):
        calls = {"n": 0}

        class FakeSC:
            fundamental_period = "Mar 2026 (annual) + TTM P&L"

        def lookup(name):
            calls["n"] += 1
            return FakeSC()

        _f, prov = q.derive_quarterly(self.raw, {}, [self.prev, self.cur],
                                      "ACME", screener_lookup=lookup)
        # EBITDA margin was computable -> python_derived, NOT screener
        self.assertEqual(prov["EBITDA Margin"]["Q2"]["source"], q.SOURCE_PY)
        # ROE is not computable -> screener consulted, then rejected -> unavailable
        self.assertEqual(prov["Return on Equity (ROE) %"]["Q2"]["source"],
                         q.SOURCE_UNAVAILABLE)
        self.assertGreater(calls["n"], 0)          # screener WAS consulted
        self.assertIn("rejected", prov["Return on Equity (ROE) %"]["Q2"]["note"].lower())


# --------------------------------------------------------------------------
# 3. end-to-end acceptance on the supplied Reliance filing
# --------------------------------------------------------------------------
def _find_sample() -> Path | None:
    for c in (Path(__file__).parent / "fixtures" / "reliance-quarterly-results.pdf",
              Path(os.environ.get("FUNDACHECK_QPDF", "")),
              Path.home() / "Downloads" / "reliance-quarterly-results.pdf"):
        if c and c.is_file():
            return c
    return None


_SAMPLE = _find_sample()
_LIBS_OK = q._HAVE_PDFPLUMBER and q._HAVE_PYMUPDF and q.ocr_available()


@unittest.skipUnless(_SAMPLE, "sample Reliance PDF not found")
@unittest.skipUnless(_LIBS_OK, "pdfplumber / PyMuPDF / OCR engine not available")
class RelianceAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = q.load_quarterly_pdf(str(_SAMPLE))
        cls.meta = cls.model.meta

    def test_identifies_company_and_symbol(self):
        self.assertIn("Reliance Industries", self.model.company)
        self.assertEqual(self.meta.get("source_symbol"), "RELIANCE")

    def test_current_and_previous_quarter(self):
        self.assertEqual(self.meta["current_period"], "Jun-2026")
        self.assertEqual(self.meta["previous_period"], "Mar-2026")
        self.assertEqual(self.meta["current_quarter_tag"], "Q1 FY27")

    def test_consolidated_not_standalone(self):
        # page 9 is the consolidated results table; page 18 is standalone
        self.assertEqual(self.meta["source_scope"], "consolidated")
        self.assertIn(9, self.meta["source_pages"])

    def test_exactly_two_periods_no_fy(self):
        self.assertEqual(list(self.model.historical.columns), ["Mar-2026", "Jun-2026"])
        for col in list(self.model.historical.columns) + list(self.model.ratios.columns):
            self.assertNotRegex(col, r"(?i)fy")

    def test_rasterised_current_quarter_ocr_values(self):
        jun = self.model.historical["Jun-2026"]
        expected = {"Sales": 311850, "Total Income": 318400, "Total Expenses": 287770,
                    "Earnings Before Tax": 30630, "Net Profit": 23196,
                    "Finance Costs": 8337, "Depreciation": 15100,
                    "Changes in Inventories": -1326}
        for line, val in expected.items():
            self.assertAlmostEqual(jun[line], val, delta=1, msg=f"{line} wrong")

    def test_identities_validated(self):
        self.assertTrue(self.meta["validated"]["Jun-2026"])
        self.assertTrue(self.meta["validated"]["Mar-2026"])

    def test_current_column_is_ocr(self):
        self.assertEqual(self.meta["column_source"]["Jun-2026"], "image")
        self.assertEqual(self.meta["column_source"]["Mar-2026"], "text")

    def test_raw_values_are_pdf_raw_provenance(self):
        prov = self.meta["raw_provenance"]["Sales"]["Jun-2026"]
        self.assertEqual(prov["source"], "pdf_raw")
        self.assertIn("ocr", prov["note"])         # current column was OCR'd

    def test_margins_are_python_derived(self):
        p = self.meta["metrics"]["EBITDA Margin"]["Jun-2026"]
        self.assertEqual(p["source"], "python_derived")
        self.assertIn("Revenue", p["formula"])

    def test_interest_coverage_validated_against_pdf(self):
        # Python EBIT/Finance must match the PDF's reported Interest Service
        # Coverage Ratio -> validation pass, in BOTH quarters.
        for per in ("Mar-2026", "Jun-2026"):
            p = self.meta["metrics"]["Interest Coverage Ratio"][per]
            self.assertEqual(p["source"], "python_derived")
            self.assertEqual(p["validation"], "pass")

    def test_debt_equity_is_pdf_reported(self):
        p = self.meta["metrics"]["Debt to Equity Ratio"]["Mar-2026"]
        self.assertEqual(p["source"], "pdf_reported_validation")

    def test_unavailable_metrics_not_fabricated(self):
        for metric in ("Return on Equity (ROE) %", "Cash Conversion Cycle", "CFO / PAT"):
            p = self.meta["metrics"][metric]["Jun-2026"]
            self.assertEqual(p["source"], "unavailable")
            self.assertIsNone(p["value"])
        self.assertTrue(self.model.series("Return on Equity (ROE) %").empty)

    def test_quarterly_scoring_is_independent_of_3y_rule(self):
        result = assess_quarterly(self.model, get_sector("generic"))
        # quarterly scoring never invents a 3-year average
        for m in result.metrics:
            self.assertIsNone(m.average_3y)
        self.assertIn(result.verdict, ("STRONG", "NEUTRAL", "WEAK"))


class ExcelStillWorks(unittest.TestCase):
    def test_demo_workbook_loads_as_annual(self):
        sample = Path(__file__).parents[1] / "sample_data" / "3S_model_sample.xlsx"
        if not sample.is_file():
            self.skipTest("demo workbook not present")
        model = load_model(str(sample))
        self.assertIsInstance(model, FinancialModel)
        self.assertFalse(model.historical.empty)
        self.assertNotEqual(model.meta.get("periodicity"), "quarterly")


if __name__ == "__main__":
    unittest.main()
