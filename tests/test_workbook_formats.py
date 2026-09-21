"""
Regression tests for workbook ingestion: format detection, line-item
normalization, statement reconstruction, derived values/ratios, missing-data
behaviour and provenance.

Every fixture here is SYNTHETIC and built in memory, so the suite runs anywhere
and none of it is tied to one company. The real Screener BHEL workbooks are used
as an acceptance test when they are present (see TestRealWorkbooks) and skipped
otherwise.
"""

from __future__ import annotations

import io
import os
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from core import derive
from core import parser as P
from core import synonyms as SYN
from core.scoring import assess
from core.sectors import classify_sector, get_sector

YEARS = ["2022-03-31", "2023-03-31", "2024-03-31", "2025-03-31", "2026-03-31"]
LABELS = ["FY22", "FY23", "FY24", "FY25", "FY26"]


# --------------------------------------------------------------------------
# synthetic workbook builders
# --------------------------------------------------------------------------
def _write(sheets: dict[str, list[list]]) -> io.BytesIO:
    """Write {sheet name: rows} to an in-memory .xlsx and rewind it."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(writer, sheet_name=name,
                                        header=False, index=False)
    buffer.seek(0)
    return buffer


def _statement_rows(labels: dict[str, list], title: str,
                    periods: list = YEARS) -> list[list]:
    """A statement sheet: title row, period header row, then one row per line."""
    rows: list[list] = [[None, title], [None, "Year", *periods]]
    for label, values in labels.items():
        rows.append([None, label, *values])
    return rows


PNL = {
    "Sales": [1000, 1100, 1250, 1400, 1600],
    "COGS": [600, 650, 720, 800, 900],
    "Selling & General Expenses": [150, 160, 180, 200, 220],
    "Depreciation": [50, 55, 60, 65, 70],
    "Interest": [30, 30, 35, 40, 45],
    "Other Income": [10, 12, 14, 16, 18],
    "Earnings Before Tax": [180, 217, 269, 311, 383],
    "Tax": [45, 54, 67, 78, 96],
    "Net Profit": [135, 163, 202, 233, 287],
    "Earnings per Share": [13.5, 16.3, 20.2, 23.3, 28.7],
}
BS = {
    "Equity Share Capital": [100, 100, 100, 100, 100],
    "Reserves": [500, 620, 780, 960, 1200],
    "Borrowings": [300, 290, 280, 300, 310],
    "Other Liabilities": [200, 210, 220, 240, 250],
    "Trade Payables": [120, 130, 140, 150, 160],
    "Net Block": [400, 420, 450, 480, 520],
    "Capital Work in Progress": [20, 25, 30, 35, 40],
    "Investments": [50, 55, 60, 65, 70],
    "Receivables": [150, 165, 180, 200, 230],
    "Inventory": [100, 110, 125, 140, 160],
    "Cash & Bank": [80, 95, 115, 140, 170],
    "Total Asset": [1220, 1350, 1520, 1700, 1920],
}
CF = {
    "Cash from Operating Activity": [160, 180, 220, 250, 300],
    "Cash from Investing Activity": [-80, -90, -100, -110, -130],
    "Cash from Financing Activity": [-40, -45, -50, -55, -60],
    "Net Cash Flow": [40, 45, 70, 85, 110],
}


def historical_workbook() -> io.BytesIO:
    """A FundaCheck/HistoricalFS-style workbook with cached values."""
    combined = {**PNL, **BS, **CF}
    return _write({
        "HistoricalFS": _statement_rows(
            combined, "Historical Financial Data - SYNTHETIC WIDGETS LTD"),
        "Ratio Analysis": _statement_rows(
            {"Sales Growth": [None, 0.10, 0.136, 0.12, 0.143]},
            "Historical Financial Data - SYNTHETIC WIDGETS LTD"),
    })


def separate_statements_workbook(pnl_name: str = "Profit & Loss",
                                 bs_name: str = "Balance Sheet",
                                 cf_name: str = "Cash Flow") -> io.BytesIO:
    return _write({
        pnl_name: _statement_rows(PNL, "SYNTHETIC WIDGETS LTD | INCOME STATEMENT"),
        bs_name: _statement_rows(BS, "SYNTHETIC WIDGETS LTD | BALANCE SHEET"),
        cf_name: _statement_rows(CF, "SYNTHETIC WIDGETS LTD | CASH FLOW"),
    })


def data_sheet_rows(overrides: dict[str, list] | None = None,
                    drop: tuple[str, ...] = ()) -> list[list]:
    """A Screener 'Data Sheet' block — the raw numbers, no formulas."""
    pnl = {
        "Sales": [1000, 1100, 1250, 1400, 1600],
        "Raw Material Cost": [560, 605, 670, 745, 840],
        "Change in Inventory": [0, 0, 0, 0, 0],
        "Power and Fuel": [20, 22, 25, 28, 30],
        "Other Mfr. Exp": [20, 23, 25, 27, 30],
        "Employee Cost": [0, 0, 0, 0, 0],
        "Selling and admin": [90, 95, 105, 115, 125],
        "Other Expenses": [60, 65, 75, 85, 95],
        "Other Income": [10, 12, 14, 16, 18],
        "Depreciation": [50, 55, 60, 65, 70],
        "Interest": [30, 30, 35, 40, 45],
        "Profit before tax": [180, 217, 269, 311, 383],
        "Tax": [45, 54, 67, 78, 96],
        "Net profit": [135, 163, 202, 233, 287],
        "Dividend Amount": [40, 48, 60, 70, 86],
    }
    bs = {
        "Equity Share Capital": BS["Equity Share Capital"],
        "Reserves": BS["Reserves"],
        "Borrowings": BS["Borrowings"],
        "Other Liabilities": BS["Other Liabilities"],
        "Net Block": BS["Net Block"],
        "Capital Work in Progress": BS["Capital Work in Progress"],
        "Investments": BS["Investments"],
        "Other Assets": [450, 495, 560, 620, 700],
        "Receivables": BS["Receivables"],
        "Inventory": BS["Inventory"],
        "Cash & Bank": BS["Cash & Bank"],
        "No. of Equity Shares": [100_000_000] * 5,
    }
    cf = dict(CF)
    if overrides:
        for block in (pnl, bs, cf):
            for key in list(block):
                if key in overrides:
                    block[key] = overrides[key]
    for key in drop:
        pnl.pop(key, None)
        bs.pop(key, None)
        cf.pop(key, None)

    rows: list[list] = [["COMPANY NAME", "SYNTHETIC WIDGETS LTD"],
                        ["META"],
                        ["Number of shares", 100_000_000],
                        ["Face Value", 10],
                        ["Current Price", 250.0],
                        ["Market Capitalization", 2500.0],
                        ["PROFIT & LOSS"],
                        ["Report Date", *YEARS]]
    rows += [[k, *v] for k, v in pnl.items()]
    # A quarterly block that must NEVER reach the annual model.
    rows += [["Quarters"],
             ["Report Date", "2025-06-30", "2025-09-30", "2025-12-31",
              "2026-03-31", "2026-06-30"],
             ["Sales", 9999, 9999, 9999, 9999, 9999],
             ["Net profit", 8888, 8888, 8888, 8888, 8888]]
    rows += [["BALANCE SHEET"], ["Report Date", *YEARS]]
    rows += [[k, *v] for k, v in bs.items()]
    rows += [["CASH FLOW:"], ["Report Date", *YEARS]]
    rows += [[k, *v] for k, v in cf.items()]
    rows += [["PRICE:", 120, 150, 180, 210, 250]]
    rows += [["DERIVED:"],
             ["Adjusted Equity Shares in Cr", 10, 10, 10, 10, 10]]
    return rows


def data_sheet_workbook(**kwargs) -> io.BytesIO:
    """A raw Screener export: formula-only statement tabs + a real Data Sheet."""
    empty = [[None, None], [None, "Narration"], [None, "Sales"]]
    return _write({
        "Profit & Loss": empty,
        "Quarters": empty,
        "Balance Sheet": empty,
        "Cash Flow": empty,
        "Data Sheet": data_sheet_rows(**kwargs),
    })


# --------------------------------------------------------------------------
# 1-5 · format + sheet-name detection
# --------------------------------------------------------------------------
class TestFormatDetection(unittest.TestCase):
    def test_historical_workbook(self):
        model = P.load_model(historical_workbook())
        self.assertEqual(model.meta["workbook_format"], P.FORMAT_HISTORICAL)
        self.assertEqual(model.years, LABELS)
        self.assertEqual(model.company, "Synthetic Widgets Ltd")

    def test_separate_statement_workbook(self):
        model = P.load_model(separate_statements_workbook())
        self.assertEqual(model.meta["workbook_format"], P.FORMAT_STATEMENTS)
        for line in ("Sales", "Borrowings", "Cash from Operating Activity"):
            self.assertIn(line, model.historical.index)

    def test_sheet_name_variations(self):
        for names in (("Profit and Loss", "Balance Sheet", "Cash Flow Statement"),
                      ("P&L", "BALANCE SHEET", "CASHFLOW"),
                      ("Income Statement", "Balance sheet", "Statement of Cash Flows"),
                      ("PROFIT & LOSS", "Balance-Sheet", "Cash_Flow")):
            with self.subTest(names=names):
                model = P.load_model(separate_statements_workbook(*names))
                self.assertIn("Sales", model.historical.index)
                self.assertIn("Cash from Operating Activity", model.historical.index)

    def test_data_sheet_rebuild(self):
        model = P.load_model(data_sheet_workbook())
        self.assertEqual(model.meta["workbook_format"], P.FORMAT_DATA_SHEET)
        self.assertTrue(model.rebuilt_from_data_sheet)
        self.assertEqual(model.years, LABELS)

    def test_unsupported_workbook_is_rejected_with_a_reason(self):
        book = _write({"Sheet1": [["Client", "Value"], ["Acme", 12]]})
        with self.assertRaises(P.ParseError) as ctx:
            P.load_model(book)
        self.assertIn("sheets found", str(ctx.exception))

    def test_decoy_sheet_is_not_read_as_the_statements(self):
        """A 'Revenue Model' tab parses fine but is not a set of statements."""
        decoy = _statement_rows(
            {"Segment A": [1, 2, 3, 4, 5], "Segment B": [1, 2, 3, 4, 5],
             "Sales": [1000, 1100, 1250, 1400, 1600]},
            "SYNTHETIC WIDGETS LTD - REVENUE MODEL")
        book = _write({
            "Revenue Model": decoy,
            "Income Statement": _statement_rows(PNL, "SYNTHETIC WIDGETS LTD"),
            "Balance Sheet": _statement_rows(BS, "SYNTHETIC WIDGETS LTD"),
        })
        model = P.load_model(book)
        self.assertIn("Net Profit", model.historical.index)
        self.assertNotIn("Segment A", model.historical.index)


# --------------------------------------------------------------------------
# 6 · metric-name variations
# --------------------------------------------------------------------------
class TestMetricNameVariations(unittest.TestCase):
    def test_alternative_line_names_still_rebuild(self):
        renamed = {
            "Revenue from Operations": PNL["Sales"],
            "Cost of Goods Sold": PNL["COGS"],
            "Depreciation & Amortisation": PNL["Depreciation"],
            "Finance Costs": PNL["Interest"],
            "Profit Before Tax": PNL["Earnings Before Tax"],
            "Income Tax Expense": PNL["Tax"],
            "Profit After Tax": PNL["Net Profit"],
            "Share Capital": BS["Equity Share Capital"],
            "Reserves & Surplus": BS["Reserves"],
            "Total Debt": BS["Borrowings"],
            "Trade Payables": BS["Trade Payables"],
            "Trade Receivables": BS["Receivables"],
            "Inventories": BS["Inventory"],
            "Cash and Cash Equivalents": BS["Cash & Bank"],
            "Property, Plant and Equipment": BS["Net Block"],
            "Total Assets": BS["Total Asset"],
            "Net Cash from Operating Activities": CF["Cash from Operating Activity"],
        }
        book = _write({"Income Statement": _statement_rows(
            renamed, "SYNTHETIC WIDGETS LTD | INCOME STATEMENT")})
        model = P.load_model(book)
        derive.fill_missing_ratios(model)
        for ratio in ("Net Profit Margin", "Return on Equity (ROE) %",
                      "Debt to Equity Ratio", "Payable Days", "CFO / Sales"):
            self.assertFalse(model.series(ratio).empty, ratio)

    def test_synonyms_never_map_other_liabilities_to_payables(self):
        self.assertEqual(SYN.resolve("Other Liabilities"), "other_liabilities")
        self.assertIsNone(
            SYN.find_label(["Other Liabilities", "Other Current Liabilities"],
                           "payables"))
        for name in ("Trade Payables", "Sundry Creditors", "Accounts Payable"):
            self.assertEqual(SYN.resolve(name), "payables", name)


# --------------------------------------------------------------------------
# 7-11 · missing / degenerate inputs
# --------------------------------------------------------------------------
class TestMissingData(unittest.TestCase):
    def _ratios(self, model):
        derive.fill_missing_ratios(model)
        return model

    def test_missing_payables_leaves_creditor_ratios_unavailable(self):
        model = self._ratios(P.load_model(data_sheet_workbook()))
        for ratio in ("Creditor Turnover Ratio", "Payable Days",
                      "Cash Conversion Cycle"):
            self.assertTrue(model.series(ratio).empty, ratio)
        reasons = derive.missing_ratio_reasons(model)
        self.assertIn("Trade Payables", reasons["Payable Days"])

    def test_payables_present_gives_the_full_working_capital_cycle(self):
        model = self._ratios(P.load_model(separate_statements_workbook()))
        for ratio in ("Creditor Turnover Ratio", "Payable Days",
                      "Cash Conversion Cycle"):
            self.assertFalse(model.series(ratio).empty, ratio)
        # CCC identity holds on the latest year.
        self.assertAlmostEqual(
            model.latest("Cash Conversion Cycle"),
            model.latest("Debtor Days") + model.latest("Inventory Days")
            - model.latest("Payable Days"), places=6)

    def test_missing_depreciation_only_drops_what_depends_on_it(self):
        model = self._ratios(
            P.load_model(data_sheet_workbook(drop=("Depreciation",))))
        self.assertTrue(model.series("Depreciation % Sales").empty)
        self.assertFalse(model.series("EBITDA Margin").empty)
        self.assertFalse(model.series("Net Profit Margin").empty)

    def test_missing_debt_leaves_leverage_ratios_unavailable(self):
        model = self._ratios(
            P.load_model(data_sheet_workbook(drop=("Borrowings",))))
        for ratio in ("Debt to Equity Ratio", "Debt to Asset Ratio",
                      "CFO / Total Debt"):
            self.assertTrue(model.series(ratio).empty, ratio)
        self.assertFalse(model.series("Return on Equity (ROE) %").empty)

    def test_zero_denominators_never_produce_infinities(self):
        zeros = {"Sales": [0, 0, 0, 0, 0]}
        model = self._ratios(P.load_model(data_sheet_workbook(overrides=zeros)))
        for name in model.ratios.index:
            values = pd.to_numeric(model.ratios.loc[name], errors="coerce")
            self.assertFalse(np.isinf(values.dropna()).any(), name)

    def test_missing_ratio_reasons_are_stated_not_invented(self):
        model = self._ratios(P.load_model(data_sheet_workbook()))
        reasons = derive.missing_ratio_reasons(model)
        for name, reason in reasons.items():
            self.assertTrue(model.series(name).empty, name)
            self.assertIn("unavailable", reason)


# --------------------------------------------------------------------------
# 12-13 · quarterly / TTM must not contaminate annual ratios
# --------------------------------------------------------------------------
class TestAnnualOnly(unittest.TestCase):
    def test_quarterly_block_is_not_mixed_into_annual_values(self):
        model = P.load_model(data_sheet_workbook())
        sales = model.historical.loc["Sales"]
        self.assertEqual(list(sales.index), LABELS)
        self.assertNotIn(9999, list(sales.values))
        self.assertNotIn(8888, list(model.historical.loc["Net Profit"].values))

    def test_ttm_column_is_kept_out_of_the_annual_deep_dive(self):
        from core.ratio_deepdive import full_years

        book = _write({"HistoricalFS": _statement_rows(
            {k: [*v, v[-1]] for k, v in {**PNL, **BS, **CF}.items()},
            "Historical Financial Data - SYNTHETIC WIDGETS LTD",
            periods=[*YEARS, "TTM"])})
        model = P.load_model(book)
        self.assertIn("TTM", model.years)
        self.assertNotIn("TTM", full_years(model))

    def test_projected_columns_are_dropped(self):
        book = _write({"Income Statement": _statement_rows(
            PNL, "SYNTHETIC WIDGETS LTD | INCOME STATEMENT",
            periods=["FY22A", "FY23A", "FY24A", "FY25E", "FY26E"])})
        model = P.load_model(book)
        self.assertEqual(model.years, ["FY22", "FY23", "FY24"])


# --------------------------------------------------------------------------
# 14 · derived values, common size and provenance
# --------------------------------------------------------------------------
class TestDerivation(unittest.TestCase):
    def test_derived_statement_lines_follow_their_stated_formula(self):
        model = P.load_model(data_sheet_workbook())
        h = model.historical
        latest = model.latest_year
        self.assertAlmostEqual(h.loc["Gross Profit", latest],
                               h.loc["Sales", latest] - h.loc["COGS", latest],
                               places=6)
        self.assertAlmostEqual(
            h.loc["EBITDA", latest],
            h.loc["Sales", latest] - h.loc["COGS", latest]
            - h.loc["Selling & General Expenses", latest], places=6)
        self.assertAlmostEqual(h.loc["EBIT (OPM)", latest],
                               h.loc["EBITDA", latest]
                               - h.loc["Depreciation", latest], places=6)
        self.assertAlmostEqual(
            h.loc["Total Asset", latest],
            h.loc[["Equity Share Capital", "Reserves", "Borrowings",
                   "Other Liabilities"], latest].sum(), places=6)

    def test_eps_uses_the_bonus_adjusted_share_count(self):
        model = P.load_model(data_sheet_workbook())
        latest = model.latest_year
        self.assertAlmostEqual(
            model.historical.loc["Earnings per Share", latest],
            model.historical.loc["Net Profit", latest] / 10.0, places=6)

    def test_common_size_is_derived_as_fractions(self):
        model = P.load_model(data_sheet_workbook())
        added = derive.fill_missing_common_size(model)
        self.assertIn("Sales", added)
        latest = model.latest_year
        self.assertAlmostEqual(model.common_size.loc["Sales", latest], 1.0, places=6)
        self.assertLess(abs(model.common_size.loc["COGS", latest]), 1.0)

    def test_provenance_separates_read_values_from_computed_ones(self):
        model = P.load_model(data_sheet_workbook())
        derive.fill_missing_ratios(model)
        self.assertEqual(model.provenance_of("Sales"), "source")
        self.assertEqual(model.provenance_of("Net Profit"), "source")
        self.assertEqual(model.provenance_of("EBITDA"), "derived")
        self.assertEqual(model.provenance_of("Return on Capital Employed (ROCE) %"),
                         "derived")
        self.assertIn("EBIT", model.provenance_formula(
            "Return on Capital Employed (ROCE) %"))

    def test_every_derived_ratio_has_a_written_formula(self):
        model = P.load_model(separate_statements_workbook())
        for name in derive.derived_ratios(model):
            self.assertIn(name, derive.RATIO_FORMULAS, name)

    def test_required_growth_and_efficiency_ratios_are_produced(self):
        model = P.load_model(separate_statements_workbook())
        derive.fill_missing_statement_lines(model)
        derive.fill_missing_ratios(model)
        expected = [
            "Sales Growth", "EBITDA Growth", "EBIT Growth", "Net Profit Growth",
            "EPS Growth", "Gross Margin", "EBITDA Margin", "EBIT Margin",
            "EBT Margin", "Net Profit Margin", "Return on Equity (ROE) %",
            "Return on Capital Employed (ROCE) %", "Return on Assets (ROA) %",
            "Debtor Turnover Ratio", "Creditor Turnover Ratio",
            "Inventory Turnover", "Fixed Asset Turnover", "Capital Turnover Ratio",
            "Debtor Days", "Inventory Days", "Payable Days",
            "Cash Conversion Cycle", "Debt to Equity Ratio", "Debt to Asset Ratio",
            "Interest Coverage Ratio", "CFO / Sales", "CFO / PAT",
            "CFO / Total Assets", "CFO / Total Debt",
        ]
        for ratio in expected:
            self.assertFalse(model.series(ratio).empty, ratio)

    def test_inventory_days_is_the_inverse_of_inventory_turnover(self):
        model = P.load_model(separate_statements_workbook())
        derive.fill_missing_ratios(model)
        self.assertAlmostEqual(model.latest("Inventory Days"),
                               365 / model.latest("Inventory Turnover"), places=6)


# --------------------------------------------------------------------------
# analyst-built models: FY spans, quarter columns, statutory line wording
# --------------------------------------------------------------------------
ANALYST_PERIODS = ["FY 2022-23", "Q1 2023-24", "Q2 2023-24", "Q3 2023-24",
                   "Q4 2023-24", "FY 2023-24", "FY 2024-25", "E FY 2025-26",
                   "E FY 2026-27"]


def _analyst_rows(labels: dict[str, list]) -> list[list]:
    """A hand-built model: mixed annual / quarterly / forecast columns, and an
    'Add'/'Less' marker column to the left of the labels."""
    rows: list[list] = [[None, "FY Mar (Rs in Crores)", *ANALYST_PERIODS]]
    for label, values in labels.items():
        rows.append(["Add", label, *values])
    return rows


def analyst_workbook() -> io.BytesIO:
    nine = lambda base: [base * m for m in (1, .25, .25, .25, .25, 1.05, 1.1, 1.2, 1.3)]
    pnl = {
        "Gross Revenue from sale of products and services": nine(1100.0),
        "REVENUE FROM OPERATIONS": nine(1000.0),
        "OTHER INCOME": nine(20.0),
        "Net Revenue": nine(1020.0),          # revenue INCLUDING other income
        "COGS": nine(600.0),
        "Gross Profit": nine(400.0),
        "Employee benefits expense": nine(150.0),
        "EBITDA": nine(250.0),
        "Depreciation and amortization expense": nine(50.0),
        "EBIT": nine(200.0),
        "Finance costs": nine(10.0),
        "PROFIT BEFORE TAX": nine(210.0),
        "TAX EXPENSE": nine(52.0),
        "Net Profit": nine(158.0),
    }
    bs = {
        "Property, Plant and Equipment": nine(500.0),
        "Inventories": nine(120.0),
        "(ii) Trade receivables": nine(150.0),
        "(iii) Cash and cash equivalents": nine(90.0),
        "Total Asset": nine(1500.0),
        "Equity Share capital": nine(100.0),
        "Other Equity": nine(900.0),
        "(i)  Borrowings": nine(40.0),        # non-current
        "(iii) Trade payables": nine(130.0),
        "Other current liabilities": nine(200.0),
    }
    # The current-liabilities section repeats the same label — the parser
    # suffixes it and the real position is the sum.
    bs_rows = _analyst_rows(bs)
    bs_rows.append(["Add", "(i)  Borrowings", *nine(60.0)])
    cf = {
        "NET CASH FROM OPERATING ACTIVITIES": nine(180.0),
        "NET CASH USED IN INVESTING ACTIVITIES": nine(-80.0),
        "NET CASH USED IN FINANCING ACTIVITIES": nine(-60.0),
        "Dividend paid": nine(-70.0),         # an outflow, reported negative
        "NET (DECREASE) / INCREASE IN CASH AND CASH EQUIVALENTS": nine(40.0),
    }
    return _write({"Income statement": _analyst_rows(pnl),
                   "Balance Sheet": bs_rows,
                   "Cash Flow": _analyst_rows(cf)})


class TestAnalystModel(unittest.TestCase):
    def setUp(self):
        self.model = P.load_model(analyst_workbook(),
                                  filename="ITC Day 10 bcm.xlsx")
        derive.fill_missing_statement_lines(self.model)
        derive.fill_missing_ratios(self.model)

    def test_fy_spans_become_financial_years(self):
        """FY 2022-23 is FY23; quarters and E-marked forecasts are dropped."""
        self.assertEqual(self.model.years, ["FY23", "FY24", "FY25"])

    def test_a_row_of_figures_is_not_mistaken_for_the_header(self):
        self.assertIn("REVENUE FROM OPERATIONS", self.model.historical.index)
        self.assertAlmostEqual(self.model.historical.loc[
            "REVENUE FROM OPERATIONS", "FY23"], 1000.0, places=6)

    def test_statutory_enumerators_are_stripped(self):
        for label, concept in (("(ii) Trade receivables", "receivables"),
                               ("(iii) Trade payables", "payables"),
                               ("(iii) Cash and cash equivalents", "cash"),
                               ("(i)  Borrowings", "borrowings")):
            self.assertEqual(SYN.resolve(label), concept, label)

    def test_sales_is_the_statutory_top_line_not_net_revenue(self):
        """'Net Revenue' here includes other income, so it must not win."""
        self.assertAlmostEqual(
            self.model.latest("Net Profit Margin"), 158.0 * 1.1 / (1000.0 * 1.1),
            places=6)

    def test_split_balance_sheet_lines_are_added_together(self):
        """Borrowings appear under both non-current and current liabilities."""
        equity = 100.0 * 1.1 + 900.0 * 1.1
        self.assertAlmostEqual(self.model.latest("Debt to Equity Ratio"),
                               (40.0 + 60.0) * 1.1 / equity, places=6)

    def test_dividend_payout_uses_the_magnitude_of_an_outflow(self):
        payout = self.model.latest("Dividend Payout %")
        self.assertGreater(payout, 0)
        self.assertAlmostEqual(payout, 70.0 / 158.0, places=6)

    def test_company_falls_back_to_the_file_name(self):
        self.assertEqual(self.model.company, "ITC")
        self.assertEqual(classify_sector(self.model.company).sector, "fmcg")

    def test_file_name_noise_is_trimmed(self):
        cases = {"ITC Day 10 bcm.xlsx": "ITC",
                 "Asian Paints FY25 model.xlsx": "Asian Paints",
                 "TCS_model_v3_final.xlsx": "TCS",
                 "Bharat Heavy Electricals - DCF.xlsx": "Bharat Heavy Electricals",
                 "2026 export.xlsx": ""}
        for name, expected in cases.items():
            self.assertEqual(P.company_from_filename(name), expected, name)

    def test_a_workbook_title_still_beats_the_file_name(self):
        model = P.load_model(historical_workbook(), filename="wrong name.xlsx")
        self.assertEqual(model.company, "Synthetic Widgets Ltd")


# --------------------------------------------------------------------------
# company identity + sector (Parts 9 and 10)
# --------------------------------------------------------------------------
class TestIdentityAndSector(unittest.TestCase):
    def test_one_company_many_spellings(self):
        names = ["Bharat Heavy Electricals Ltd", "BHEL", "BHEL LTD",
                 "BHARAT HEAVY ELECTRICALS LIMITED"]
        sectors = {classify_sector(n).sector for n in names}
        self.assertEqual(len(sectors), 1)
        detection = classify_sector(names[0])
        self.assertEqual(detection.confidence, "high")
        self.assertEqual(detection.source, "known_company")

    def test_identity_beats_financial_shape(self):
        """A confident company match is never overridden by the ratios."""
        misleading = {"Debt to Equity Ratio": 9.0, "Interest % Sales": 0.9}
        self.assertEqual(classify_sector("ITC Ltd", misleading).sector, "fmcg")


# --------------------------------------------------------------------------
# end-to-end: a raw workbook reaches the scoring engine
# --------------------------------------------------------------------------
class TestEndToEnd(unittest.TestCase):
    def test_raw_workbook_scores(self):
        model = P.load_model(data_sheet_workbook())
        derive.fill_missing_ratios(model)
        derive.fill_missing_common_size(model)
        detection = classify_sector(model.company, {})
        result = assess(model, get_sector(detection.sector))
        self.assertGreater(result.total_score, 0)
        self.assertIn(result.verdict, ("STRONG", "NEUTRAL", "WEAK"))
        # Unavailable inputs are reported as gaps, never scored as zero.
        self.assertIn("Cash Conversion Cycle", result.data_gaps)


# --------------------------------------------------------------------------
# 15 · the real Screener workbooks, when they are available
# --------------------------------------------------------------------------
def _find_fixture(*names: str) -> Path | None:
    roots = [Path(__file__).parent / "fixtures",
             Path(os.environ.get("FUNDACHECK_XLSX", "")),
             Path.home() / "Downloads"]
    for root in roots:
        if not root or not root.exists():
            continue
        for name in names:
            candidate = root / name
            if candidate.exists():
                return candidate
    return None


RAW_BHEL = _find_fixture("B H E L (1).xlsx", "BHEL_raw.xlsx")
STRUCTURED_BHEL = _find_fixture("B H E L.xlsx", "BHEL.xlsx")


@unittest.skipIf(RAW_BHEL is None, "raw Screener workbook not available")
class TestRealWorkbooks(unittest.TestCase):
    def test_raw_screener_workbook_reconstructs_the_annual_model(self):
        model = P.load_model(str(RAW_BHEL))
        derive.fill_missing_ratios(model)
        derive.fill_missing_common_size(model)
        self.assertEqual(model.company, "BHARAT HEAVY ELECTRICALS LTD")
        self.assertTrue(all(y.startswith("FY") for y in model.years))
        for line in ("Sales", "Net Profit", "EBITDA", "Total Asset",
                     "Cash from Operating Activity"):
            self.assertIn(line, model.historical.index)
        self.assertFalse(model.common_size.empty)
        self.assertGreater(len(model.ratios), 25)
        self.assertEqual(classify_sector(model.company).sector, "infrastructure")

    @unittest.skipIf(STRUCTURED_BHEL is None, "structured workbook not available")
    def test_raw_and_structured_workbooks_agree(self):
        raw = P.load_model(str(RAW_BHEL))
        structured = P.load_model(str(STRUCTURED_BHEL))
        for model in (raw, structured):
            derive.fill_missing_ratios(model)
        self.assertEqual(raw.years, structured.years)
        common = raw.historical.index.intersection(structured.historical.index)
        self.assertGreater(len(common), 20)
        pd.testing.assert_frame_equal(
            raw.historical.loc[common].astype("float64"),
            structured.historical.loc[common].astype("float64"))


if __name__ == "__main__":
    unittest.main()
