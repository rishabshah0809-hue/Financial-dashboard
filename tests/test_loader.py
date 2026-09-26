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
        self.assertIn('class="fcl-src"', self.html)

    def test_he_sits_in_a_thin_circle_not_a_card(self):
        """Centred circle with the words underneath — no card around it."""
        self.assertIn("ctx.arc(CX, CY, R,", self.html)
        self.assertNotIn("border-radius:18px", self.html)
        stage = self.html.index('class="fcl-stage"')
        self.assertLess(stage, self.html.index('class="fcl-title"'))
        self.assertLess(self.html.index('class="fcl-title"'),
                        self.html.index('class="fcl-msg"'))

    def test_the_analyst_himself_is_animated(self):
        for part in ("blinking", "drawEyes", "drawMouth", "LENSES", "breath"):
            self.assertIn(part, self.html, part)

    def test_he_acts_it_out_himself_not_with_an_icon(self):
        self.assertNotIn("fcl-badge", self.html)
        for face in ("'smile'", "'grin'", "'hmm'", "'o'", "'up'", "'down'"):
            self.assertIn(face, self.html, face)

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
        """The step pills name the step; nothing reads as a percentage done."""
        self.assertNotRegex(self.html, r"\d+%\s*(complete|done)")
        self.assertNotIn("progress", self.html.split("<script>")[0].lower())

    def test_it_always_animates(self):
        """The OS reduce-motion switch (on by default on many Windows PCs)
        froze him into still frames; he must keep moving regardless."""
        self.assertNotIn("prefers-reduced-motion", self.html)
        self.assertIn("requestAnimationFrame(frame)", self.html)

    def test_his_eyes_glide_rather_than_jump(self):
        self.assertIn("moveEyes(a, dt)", self.html)

    def test_he_moves_like_one_bouncy_body(self):
        """Squash & stretch hop between steps, head on a spring behind him."""
        for part in ("function hop(t)", "startHop(now, 1)", "ctx.scale(sx, sy)",
                     "c.rotate(tilt)"):
            self.assertIn(part, self.html, part)

    def test_the_placeholder_holds_the_first_caption_before_script_runs(self):
        first = L.PHASES["note"][0][0]
        self.assertIn(f'<div class="fcl-msg">{first}</div>', self.html)

    def test_one_step_pill_per_phase_first_one_active(self):
        steps = self.html.split('class="fcl-steps">')[1].split("</div>")[0]
        self.assertEqual(steps, '<i data-step="0" class="on"></i>'
                                '<i data-step="1"></i><i data-step="2"></i>')

    def test_a_caption_cannot_close_the_script_early(self):
        html = L.loader_html("x", [("</script><b>", L.THINK)])
        self.assertNotIn("</script><b>", html.split("<script>", 1)[1].rsplit("</script>", 1)[0])

    def test_it_stays_small_enough_to_inline_on_every_render(self):
        # Mostly the base64 avatar; keep an eye on it so the loader never
        # becomes the heaviest thing on the page.
        self.assertLess(len(self.html), 400_000)


class TestWelcome(unittest.TestCase):
    def setUp(self):
        self.html = L.welcome_html()

    def test_one_card_per_thing_he_demonstrates(self):
        self.assertEqual(self.html.count('class="wc-card '), 1)
        self.assertEqual(self.html.count('data-step="'), len(L.WELCOME_PHASES))
        self.assertIn('class="wc-card on" data-step="0"', self.html)

    def test_it_is_the_same_animated_analyst(self):
        self.assertIn("data:image/png;base64,", self.html)
        self.assertIn("drawSearch", self.html)
        self.assertIn("Drop a 3-statement model into the sidebar", self.html)


class TestSectorLensJob(unittest.TestCase):
    def test_sector_lens_has_its_own_steps(self):
        self.assertEqual([m for _t, m in L.PHASES["sector"]],
                         [L.SEARCH, L.THINK, L.WRITE])


if __name__ == "__main__":
    unittest.main()
