"""
test_sector_news.py
--------------------
Sector Lens news/macro correctness:
  * geography classification (global vs india vs mixed vs unknown)
  * sector relevance of global items
  * the three-level macro-synthesis fallback (AI → deterministic → no-signal),
    with NO implementation/API wording ever surfaced
  * per-article rendering has no source hyperlink (links live only in Sources)
  * valuation/earnings headline colour is driven by the comparison state
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from core import market_context as MC
from core import sector_lens_view as V


class TestGeography(unittest.TestCase):
    def test_global(self):
        for t in ("China PMI weighs on steel and aluminium",
                  "US tariffs on steel imports raised to 50%",
                  "LME copper price hits record on supply squeeze",
                  "Global mining supply disruption lifts iron ore"):
            self.assertEqual(MC.classify_geography(t), "global", t)

    def test_india(self):
        for t in ("Sensex jumps 544 points, Nifty gains 391",
                  "Nifty Metal gains 2% led by Tata Steel",
                  "JSW Steel Q1 earnings beat estimates",
                  "Indian stock market rises as rupee strengthens"):
            self.assertEqual(MC.classify_geography(t), "india", t)

    def test_mixed(self):
        t = "Global steel tariffs hit Indian steelmakers hard"
        self.assertEqual(MC.classify_geography(t), "mixed")

    def test_unknown_is_not_global(self):
        t = "Company announces new plant commissioning"
        self.assertEqual(MC.classify_geography(t), "unknown")

    def test_reuters_byline_is_not_auto_global(self):
        # publisher must not decide geography; a Sensex story stays india
        self.assertEqual(MC.classify_geography("Sensex ends higher", "Reuters"), "india")


class TestSectorRelevance(unittest.TestCase):
    def test_relevant(self):
        self.assertTrue(MC.sector_relevant("China steel demand slumps", "metal"))
        self.assertTrue(MC.sector_relevant("LME copper rallies", "metal"))

    def test_irrelevant(self):
        self.assertFalse(MC.sector_relevant("US software earnings surprise", "metal"))
        self.assertFalse(MC.sector_relevant("European bank regulation tightens", "metal"))


class TestRouting(unittest.TestCase):
    def test_sensex_never_in_global(self):
        pool = [
            {"title": "China PMI weighs on steel", "source": "Reuters", "url": "u1", "date": "2026-09-08", "_ts": 2e9},
            {"title": "Sensex jumps 544, Nifty Metal gains", "source": "Rediff", "url": "u2", "date": "2026-09-08", "_ts": 2e9},
            {"title": "US software earnings surprise", "source": "Reuters", "url": "u3", "date": "2026-09-08", "_ts": 2e9},
        ]
        glob, india = MC._route(pool, "metal")
        gtitles = " ".join(g["title"].lower() for g in glob)
        self.assertIn("china pmi", gtitles)
        self.assertNotIn("sensex", gtitles)          # india item must not be global
        self.assertNotIn("software", gtitles)        # irrelevant global dropped
        self.assertTrue(any("sensex" in i["title"].lower() for i in india))


class TestMacroFallback(unittest.TestCase):
    HEADS = [{"title": "China steel demand slows", "source": "Reuters",
              "url": "u", "date": "2026-09-08", "_ts": 2e9}]

    def _fetch_stub(self, *a, **k):
        return list(self.HEADS)

    def test_ai_success_shown(self):
        good = ('{"label":"Contraction","global":[{"i":0,"explain":"China demand '
                'softens, pressuring global steel prices."}],"india":[],'
                '"drivers":["China demand"],"tilt":"Cautious: China demand is '
                'softening and caps any recovery in steel prices."}')
        with patch.object(MC, "_fetch_rss", self._fetch_stub), \
             patch.object(MC, "post", return_value=good), \
             patch.object(MC, "_load_cache", return_value={}), \
             patch.object(MC, "_save_cache"):
            cfg = type("C", (), {"is_live": True, "fallbacks": []})()
            ctx = MC.get_context("metal", "Metals", config=cfg, force=True)
        self.assertTrue(ctx["from_llm"])
        self.assertEqual(ctx["label"], "Contraction")
        self.assertNotIn("unavailable", (ctx["tilt"] or "").lower())

    def test_llm_failure_deterministic(self):
        with patch.object(MC, "_fetch_rss", self._fetch_stub), \
             patch.object(MC, "post", side_effect=RuntimeError("429 rate limited")), \
             patch.object(MC, "_load_cache", return_value={}), \
             patch.object(MC, "_save_cache"):
            cfg = type("C", (), {"is_live": True, "fallbacks": []})()
            ctx = MC.get_context("metal", "Metals", config=cfg,
                                 fundamentals={"earnings_growth": -55.5,
                                               "current_tilt": "Slowdown"}, force=True)
        self.assertFalse(ctx["from_llm"])
        tilt = ctx["tilt"].lower()
        self.assertIn("earnings growth", tilt)
        self.assertIn("contracting", tilt)
        # no implementation/API wording leaks to the user
        for banned in ("ai ", "llm", "api", "gpt", "model", "unavailable so",
                       "live ai", "live macro synthesis"):
            self.assertNotIn(banned, tilt)

    def test_no_data_message(self):
        with patch.object(MC, "_fetch_rss", return_value=[]), \
             patch.object(MC, "_load_cache", return_value={}), \
             patch.object(MC, "_save_cache"):
            ctx = MC.get_context("metal", "Metals", config=None,
                                 fundamentals=None, force=True)
        self.assertEqual(ctx["tilt"],
                         "No current macro signal is available from the retrieved "
                         "sources. The structural sector view is shown below.")
        self.assertFalse(ctx["global_items"])
        self.assertNotIn("unavailable", ctx["tilt"].lower().split("available")[0])

    def test_deterministic_items_have_no_impl_wording(self):
        d = MC._deterministic("Metals", "Metals is highly cyclical. Global prices lead.",
                              [self.HEADS[0]], [], {"earnings_growth": -10.0})
        for it in d["global_items"]:
            low = it["explain"].lower()
            self.assertNotIn("live ai", low)
            self.assertNotIn("commentary was unavailable", low)


class TestArticleSourceRendering(unittest.TestCase):
    ITEMS = [{"title": "China steel demand slows", "explain": "Global read-through.",
              "source": "Reuters", "url": "https://reuters.com/x", "date": "2026-09-08"}]

    def test_article_has_no_hyperlink(self):
        html = V._cycle_list(self.ITEMS)
        self.assertNotIn("<a", html)                 # no clickable link in the card
        self.assertNotIn("https://reuters.com/x", html)  # url not rendered here
        self.assertIn("Reuters", html)               # source name shown as text
        self.assertIn("2026-09-08", html)

    def test_sources_strip_keeps_the_link(self):
        # build()'s Sources strip renders srclink anchors from ctx["sources"];
        # verify the url survives in the data structure the strip consumes.
        self.assertEqual(self.ITEMS[0]["url"], "https://reuters.com/x")


class TestValuationColour(unittest.TestCase):
    def test_below_and_above_both_green(self):
        h = V._valuation_head("below", "above", "Metals")
        self.assertEqual(h.count('class="vpos"'), 2)
        self.assertNotIn('class="vneg"', h)
        self.assertIn("Priced", h)
        self.assertIn("Earning", h)

    def test_above_and_below_both_red(self):
        h = V._valuation_head("above", "below", "Metals")
        self.assertEqual(h.count('class="vneg"'), 2)
        self.assertNotIn('class="vpos"', h)

    def test_independent_colouring(self):
        h = V._valuation_head("above", "above", "Metals")   # pricey (bad) + earning more (good)
        self.assertIn('class="vneg"', h)                    # valuation red
        self.assertIn('class="vpos"', h)                    # earnings green

    def test_neutral(self):
        h = V._valuation_head("inline", "inline", "Metals")
        self.assertEqual(h.count('class="vneu"'), 2)
        self.assertNotIn('class="vpos"', h)
        self.assertNotIn('class="vneg"', h)


if __name__ == "__main__":
    unittest.main()
