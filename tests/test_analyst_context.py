"""The 'ask the analyst' answer: plain prose out, and the whole model in."""

import unittest

import pandas as pd

from core import llm
from core import ratio_deepdive as RD
from core import sectors


class TestUnwrapAnswer(unittest.TestCase):
    def test_json_wrapped_answer_is_unwrapped(self):
        raw = '{\n"answer": "Gross margin fell because pricing weakened."\n}'
        self.assertEqual(llm.unwrap_answer(raw),
                         "Gross margin fell because pricing weakened.")

    def test_code_fenced_json_is_unwrapped(self):
        self.assertEqual(llm.unwrap_answer('```json\n{"answer":"Hello."}\n```'),
                         "Hello.")

    def test_other_answer_keys_are_recognised(self):
        for key in ("response", "text", "reply", "explanation"):
            self.assertEqual(llm.unwrap_answer('{"%s": "Fine."}' % key), "Fine.")

    def test_plain_prose_passes_through_untouched(self):
        text = "The company earns 12% on equity, which is healthy for a lender."
        self.assertEqual(llm.unwrap_answer(text), text)

    def test_ambiguous_json_is_left_alone_rather_than_guessed(self):
        raw = '{"a": "one", "b": "two"}'
        self.assertEqual(llm.unwrap_answer(raw), raw)

    def test_empty_and_broken_input_is_safe(self):
        self.assertEqual(llm.unwrap_answer(""), "")
        self.assertEqual(llm.unwrap_answer("{not json"), "{not json")


class _FakeModel:
    def __init__(self, frame):
        self.ratios = frame
        self.historical = pd.DataFrame()
        self.common_size = pd.DataFrame()

    def series(self, metric):
        if metric in self.ratios.index:
            return self.ratios.loc[metric].dropna()
        return pd.Series(dtype="float64")


class TestFullRatioContext(unittest.TestCase):
    def setUp(self):
        self.model = _FakeModel(pd.DataFrame(
            {"FY17": [0.958, 0.38, 19.0, 0.14],
             "FY26": [0.488, 0.30, 23.0, 0.126]},
            index=["Gross Margin", "Capital Turnover Ratio", "Debtor Days",
                   "Return on Equity (ROE) %"]))

    def test_every_ratio_is_listed_not_only_the_scored_ones(self):
        block = llm.full_ratio_context(self.model)
        for name in self.model.ratios.index:
            self.assertIn(name, block, name)

    def test_percent_ratios_are_scaled_and_turnover_ratios_are_not(self):
        block = llm.full_ratio_context(self.model)
        self.assertIn("Gross Margin: 95.8% in FY17 -> 48.8% in FY26", block)
        self.assertIn("Capital Turnover Ratio: 0.38x in FY17 -> 0.30x in FY26", block)
        self.assertIn("Debtor Days: 19 days in FY17 -> 23 days in FY26", block)

    def test_it_states_the_movement_so_why_did_it_fall_can_be_answered(self):
        self.assertIn("first year -> latest year",
                      llm.full_ratio_context(self.model))

    def test_no_ratios_yields_no_block(self):
        self.assertEqual(llm.full_ratio_context(_FakeModel(pd.DataFrame())), "")


class TestSectorApplicability(unittest.TestCase):
    def test_lender_notes_cover_the_ratios_that_do_not_apply(self):
        notes = sectors.not_meaningful_metrics("banking")
        for metric in ("Return on Capital Employed (ROCE) %", "Inventory Days",
                       "Cash Conversion Cycle", "Gross Margin"):
            self.assertIn(metric, notes, metric)
            self.assertGreater(len(notes[metric]), 40, metric)

    def test_a_sector_without_exclusions_returns_nothing(self):
        self.assertEqual(sectors.not_meaningful_metrics("fmcg"), {})
        self.assertIsNone(sectors.metric_note("fmcg", "Inventory Days"))

    def test_the_prompt_block_tells_the_model_not_to_say_data_is_missing(self):
        block = llm.sector_applicability_context("banking")
        self.assertIn("does not apply", block)
        self.assertIn("instead of saying the data is missing", block)
        self.assertEqual(llm.sector_applicability_context("fmcg"), "")

    def test_deep_dive_explains_the_sector_reason_not_a_missing_file(self):
        msg = RD._na_reason({"sector_key": "banking"}, "Cash Conversion Cycle",
                            "Working-capital days are unavailable in this workbook.")
        self.assertIn("not a meaningful measure", msg)
        self.assertNotIn("unavailable in this workbook", msg)

    def test_deep_dive_still_blames_the_file_when_that_is_the_real_reason(self):
        fallback = "Working-capital days are unavailable in this workbook."
        self.assertEqual(
            RD._na_reason({"sector_key": "fmcg"}, "Cash Conversion Cycle", fallback),
            fallback)

    def test_unavailable_block_does_not_add_nothing_assumed_to_a_sector_reason(self):
        sector_msg = RD._na_reason({"sector_key": "banking"},
                                   "Cash Conversion Cycle", "x")
        self.assertNotIn("nothing assumed", RD._unavail(sector_msg))
        self.assertIn("nothing assumed", RD._unavail("Not in this workbook"))


if __name__ == "__main__":
    unittest.main()


class TestNotMeaningfulIsShownBesideTheNumber(unittest.TestCase):
    """The number is never hidden — a bracket is added next to it."""

    def test_deep_dive_tags_the_label_and_keeps_the_value(self):
        self.assertEqual(RD._na_tag("banking", "Fixed Asset Turnover"),
                         RD.NOT_MEANINGFUL_TAG)
        self.assertEqual(RD._na_tag("banking", "Return on Equity (ROE) %"), "")
        self.assertEqual(RD._na_tag("fmcg", "Fixed Asset Turnover"), "")

    def test_ratios_the_sector_bands_already_handle_are_not_tagged(self):
        """A lender's D/E and interest cover have purpose-built bands, so
        marking them 'not meaningful' would contradict the scorer."""
        for metric in ("Debt to Equity Ratio", "Interest Coverage Ratio"):
            self.assertEqual(RD._na_tag("banking", metric), "", metric)
            self.assertIsNone(sectors.metric_note("banking", metric), metric)

    def test_gap_bar_shows_the_value_with_the_bracket(self):
        from core import sector_lens_view as V
        html = V._dev("ROCE", 10.99, None, "pct", note="A lender…",
                      not_meaningful=True)
        self.assertIn("10.99%", html)
        self.assertIn("(not meaningful)", html)
        self.assertIn("Not meaningful", html)

    def test_gap_bar_without_the_flag_is_unchanged(self):
        from core import sector_lens_view as V
        html = V._dev("ROCE", 10.99, None, "pct")
        self.assertIn("10.99%", html)
        self.assertNotIn("(not meaningful)", html)

    def test_overview_drivers_tag_uses_the_same_table(self):
        from core import shell as SH

        class _R:
            sector = type("S", (), {"name": "Banking & Financial Services"})()
        self.assertEqual(SH._na_tag(_R(), "Fixed Asset Turnover"),
                         " (not meaningful)")
        self.assertEqual(SH._na_tag(_R(), "Return on Assets (ROA) %"), "")
