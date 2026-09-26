"""The 'analyst at work' loading screen (core.loader)."""

import re
import unittest

from core import loader as L


class TestPhases(unittest.TestCase):
    def test_every_job_has_captions_and_a_matching_action(self):
        for kind, phases in L.PHASES.items():
            self.assertTrue(phases, kind)
            for text, mode in phases:
                self.assertTrue(text.strip(), kind)
                self.assertIn(mode, (L.SEARCH, L.THINK, L.WRITE), f"{kind}:{text}")

    def test_a_job_reads_look_up_then_think_then_write(self):
        """The props tell the story of the work, not a random shuffle."""
        self.assertEqual([m for _t, m in L.PHASES["note"]],
                         [L.SEARCH, L.THINK, L.WRITE])

    def test_bare_captions_are_accepted_and_default_to_thinking(self):
        self.assertEqual(L._normalise(["Working"]), [["Working", L.THINK]])

    def test_an_unknown_mode_falls_back_rather_than_reaching_the_page(self):
        self.assertEqual(L._normalise([("x", "dancing")]), [["x", L.THINK]])

    def test_no_phases_still_produces_a_usable_loader(self):
        self.assertEqual(L._normalise(()), [list(p) for p in L.DEFAULT_PHASES])


class TestLoaderHtml(unittest.TestCase):
    def setUp(self):
        self.html = L.loader_html("Writing analyst report", L.PHASES["note"])

    def test_it_is_one_self_contained_document(self):
        self.assertTrue(self.html.startswith("<!doctype html>"))
        self.assertIn("<style>", self.html)
        self.assertIn("<script>", self.html)
        self.assertNotIn("<link", self.html)      # no external stylesheet
        self.assertNotIn("src=\"http", self.html)  # no external script

    def test_the_avatar_is_the_real_pixel_portrait(self):
        self.assertIn("data:image/png;base64,", self.html)
        self.assertIn("image-rendering:pixelated", self.html)

    def test_all_three_props_ship_with_the_page(self):
        for fn in ("drawSearch", "drawThink", "drawWrite"):
            self.assertIn(fn, self.html, fn)

    def test_the_captions_are_handed_to_the_script(self):
        for text, _m in L.PHASES["note"]:
            self.assertIn(text, self.html, text)

    def test_the_title_is_escaped(self):
        html = L.loader_html("<script>alert(1)</script>", L.PHASES["note"])
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_it_never_claims_a_percentage_it_cannot_know(self):
        """The bar marches; it must not read as progress towards completion."""
        self.assertNotRegex(self.html, r"\d+%\s*(complete|done)")
        self.assertIn("fclMarch", self.html)

    def test_reduced_motion_keeps_the_scene_but_stops_it_moving(self):
        self.assertIn("prefers-reduced-motion", self.html)
        self.assertIn("REDUCED ? FROZEN : t", self.html)

    def test_the_placeholder_holds_the_first_caption_before_script_runs(self):
        first = L.PHASES["note"][0][0]
        self.assertIn(f'<div class="fcl-msg">{first}', self.html)

    def test_it_stays_small_enough_to_inline_on_every_render(self):
        # Mostly the base64 avatar; keep an eye on it so the loader never
        # becomes the heaviest thing on the page.
        self.assertLess(len(self.html), 400_000)


if __name__ == "__main__":
    unittest.main()
