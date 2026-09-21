"""Rendering rules for the Sector Lens gap bars and the seasonality strip."""

import unittest

from core import sector_lens_view as V


class TestGapBars(unittest.TestCase):
    def test_missing_company_value_says_so(self):
        html = V._dev("P/B", None, 2.4, "x")
        self.assertIn("you <b>&mdash;</b>", html)
        self.assertIn("Not available", html)

    def test_missing_sector_benchmark_still_shows_the_company_value(self):
        """A lender has no sector ROCE — but its OWN ROCE is in the model and
        must not be reported as missing from the workbook."""
        html = V._dev("ROCE", 10.99, None, "pct",
                      note="ROCE is not a meaningful metric for lenders.")
        self.assertIn("10.99%", html)
        self.assertIn("No sector benchmark", html)
        self.assertNotIn("Not in the uploaded model", html)

    def test_both_present_draws_a_gap_bar(self):
        html = V._dev("ROE", 18.0, 12.0, "pct")
        self.assertIn("dev-bar", html)
        self.assertIn("6.00 pp", html)


class TestSeasonalityStrip(unittest.TestCase):
    def _strip(self, *states):
        cells = [{"month": f"M{i}", "state": s} for i, s in enumerate(states)]
        return V._months_strip({"cells": cells})

    def test_good_months_rise_and_weak_months_hang_below(self):
        for state in ("Strong", "Positive", "Neutral"):
            html = self._strip(state)
            self.assertIn('<div class="mo-up"><i', html, state)
            self.assertIn('<div class="mo-dn"></div>', html, state)
        for state in ("Soft", "Weak"):
            html = self._strip(state)
            self.assertIn('<div class="mo-up"></div>', html, state)
            self.assertIn('<div class="mo-dn"><i', html, state)

    def test_weak_hangs_lower_than_soft(self):
        self.assertGreater(V._STATE_BLOCK["Weak"][0], V._STATE_BLOCK["Soft"][0])
        self.assertGreater(V._STATE_BLOCK["Strong"][0],
                           V._STATE_BLOCK["Positive"][0])

    def test_both_halves_are_reserved_in_the_css(self):
        self.assertIn(f".months .mo-up{{height:{V._STATE_MAX_UP}px", V._EXTRA_CSS)
        self.assertIn(f".months .mo-dn{{height:{V._STATE_MAX_DOWN}px", V._EXTRA_CSS)

    def test_legend_carries_the_direction_cue(self):
        legend = V._seas_legend()
        self.assertEqual(legend.count("&#9650;"), 3)   # Strong, Positive, Neutral
        self.assertEqual(legend.count("&#9660;"), 2)   # Soft, Weak


if __name__ == "__main__":
    unittest.main()
