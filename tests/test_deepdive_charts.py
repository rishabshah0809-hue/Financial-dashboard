"""The Ratio Deep Dive's chart script: one bad chart must not kill the page."""

import unittest

from core import ratio_deepdive as RD

BASE = {
    "margins": {"Gross": [1, 2], "EBITDA": [1, 2]},
    "returns": {"ROE": [1, 2], "ROCE": [1, 2]},
    "de": [1, 2], "ic": [1, 2],
    "cfo": [1, 2], "cfi": [1, 2], "cff": [1, 2],
    "dd": [1, 2], "iv": [1, 2], "pay": None, "ccc": None,
    "assets": [("Net block", "#0F5B34", [1, 2])],
    "liab": [("Borrowings", "#8F3B31", [1, 2])],
    "years": ["FY25", "FY26"],
    "turnover": [("Debtor turnover", 1.0, 1.0)],
}


class TestChartScript(unittest.TestCase):
    def _js(self, **over):
        ctx = dict(BASE)
        ctx.update(over)
        return RD._render_js(ctx)

    def test_no_working_capital_card_means_no_working_capital_legend(self):
        """Without a cash cycle the card is never rendered, so touching its
        legend would throw and stop every chart after it."""
        js = self._js(ccc=None)
        self.assertNotIn("lg-wc", js)

    def test_the_balance_sheet_charts_are_still_drawn_without_a_cash_cycle(self):
        js = self._js(ccc=None)
        self.assertIn('stackedArea("ch-assets"', js)
        self.assertIn('stackedArea("ch-liab"', js)

    def test_the_working_capital_chart_returns_when_the_cycle_exists(self):
        js = self._js(ccc=[10, 12])
        self.assertIn("lg-wc", js)
        self.assertIn('wcChart("ch-wc"', js)

    def test_every_draw_call_is_individually_guarded(self):
        js = self._js(ccc=[10, 12])
        draws = [line for line in js.splitlines() if "Chart(" in line
                 or "stackedArea(" in line or "groupedBars(" in line
                 or "lineChart(" in line]
        self.assertTrue(draws)
        for line in draws:
            self.assertTrue(line.startswith("try{"), line[:60])
            self.assertIn("catch(e)", line)

    def test_the_helper_definitions_are_not_wrapped(self):
        js = self._js()
        self.assertTrue(js.startswith("var pc "), js[:40])


if __name__ == "__main__":
    unittest.main()
