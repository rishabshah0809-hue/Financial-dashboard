"""Tests for the financial synonym / normalization layer (core.synonyms)."""

import unittest

from core import synonyms as SYN


class TestNormalize(unittest.TestCase):
    def test_formatting_folds_to_same_key(self):
        for v in ("Net Sales", "NET SALES", " net sales ", "Net-Sales", "Net  Sales"):
            self.assertEqual(SYN.normalize(v), "net sales")

    def test_ampersand_and_slash(self):
        self.assertEqual(SYN.normalize("Cash & Bank"), "cash and bank")
        self.assertEqual(SYN.normalize("Depreciation / Amortisation"),
                         "depreciation amortisation")


class TestResolve(unittest.TestCase):
    def test_sales_synonyms(self):
        for v in ("Sales", "Revenue", "Total Revenue", "Net Sales", "Net Revenue",
                  "Sales Revenue", "Revenue from Operations", "Operating Revenue",
                  "Turnover", "Revenue from Operations (Net)"):
            self.assertEqual(SYN.resolve(v), "sales", v)

    def test_core_concepts(self):
        cases = {
            "Cost of Sales": "cogs", "Cost of Revenue": "cogs",
            "Gross Profit": "gross_profit", "Gross Profit / (Loss)": "gross_profit",
            "EBITDA": "ebitda", "Operating Profit": "ebit", "PBIT": "ebit",
            "Depreciation & Amortization": "depreciation",
            "Finance Costs": "interest", "Borrowing Costs": "interest",
            "PBT": "profit_before_tax", "Profit Before Taxation": "profit_before_tax",
            "Provision for Tax": "tax", "Income Tax Expense": "tax",
            "Profit After Tax": "net_profit", "PAT": "net_profit", "Net Income": "net_profit",
            "Total Debt": "borrowings", "Loans and Borrowings": "borrowings",
            "Trade Receivables": "receivables", "Sundry Debtors": "receivables",
            "Inventories": "inventory", "Stock": "inventory",
            "Cash and Cash Equivalents": "cash", "Bank Balances": "cash",
            "Marketable Securities": "investments",
            "Property, Plant and Equipment": "net_block", "PPE": "net_block",
            "CWIP": "cwip", "Capital Work-in-Progress": "cwip",
            "Share Capital": "equity_share_capital",
            "Reserves and Surplus": "reserves",
            "Total Assets": "total_assets",
        }
        for label, concept in cases.items():
            self.assertEqual(SYN.resolve(label), concept, label)

    def test_near_but_different_not_merged(self):
        # margin / % / growth / rate lines must NOT map to a monetary concept
        for v in ("Gross Margin", "Gross Profit Margin", "Gross Margin %",
                  "EBITDA Margin", "Net Profit Margin", "Sales Growth",
                  "EPS Growth %", "Effective Tax Rate", "Depreciation%Sales",
                  "Interest % Sales", "Deferred Tax"):
            self.assertIsNone(SYN.resolve(v), v)

    def test_unknown_label_unmapped(self):
        self.assertIsNone(SYN.resolve("Goodwill Impairment"))
        self.assertIsNone(SYN.resolve(""))


class TestFindLabel(unittest.TestCase):
    def test_priority_prefers_earlier_synonym(self):
        # both present -> the canonical/earlier synonym ("Sales") wins
        avail = ["Revenue", "Sales", "Turnover"]
        self.assertEqual(SYN.find_label(avail, "sales"), "Sales")

    def test_token_reorder_still_matches(self):
        self.assertEqual(SYN.find_label(["Reserves and Surplus"], "reserves"),
                         "Reserves and Surplus")

    def test_absent_concept_returns_none(self):
        self.assertIsNone(SYN.find_label(["Something Else"], "sales"))

    def test_margin_not_picked_for_monetary(self):
        # only a margin line available -> gross_profit stays unresolved
        self.assertIsNone(SYN.find_label(["Gross Margin", "Gross Margin %"], "gross_profit"))


if __name__ == "__main__":
    unittest.main()
