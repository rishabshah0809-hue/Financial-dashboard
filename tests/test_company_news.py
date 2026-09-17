"""Tests for the Company News & Outlook layer (core.company_news).

Network is never touched here: the pipeline (identity, queries, relevance,
validation, dedup, classification, ranking, rendering) is exercised on mock raw
articles. Live retrieval is validated manually, not in CI.
"""
from __future__ import annotations

import time

import pytest

from core import company_news as CN


NOW = time.time()


def _raw(title, source, url, desc="", days=3):
    return {"title": title, "source": source, "url": url, "description": desc,
            "date": "2026-09-14", "_ts": NOW - days * 86400}


# 2. company identity normalization ----------------------------------------
def test_identity_normalizes_suffix_and_symbol():
    idn = CN.resolve_identity("ITC Limited", "ITC")
    assert idn.name == "ITC"
    assert idn.symbol == "ITC"
    assert "ITC" in idn.aliases


# 3. ITC aliases resolve to one identity -----------------------------------
def test_itc_aliases_resolve_same():
    a = CN.resolve_identity("ITC Ltd", "ITC")
    b = CN.resolve_identity("ITC", "ITC")
    assert a.name == b.name == "ITC"
    assert a.symbol == b.symbol == "ITC"


# 4. TCS aliases resolve to TCS --------------------------------------------
def test_tcs_identity():
    idn = CN.resolve_identity("Tata Consultancy Services Limited", "TCS")
    assert idn.symbol == "TCS"
    assert idn.name.lower().startswith("tata consultancy")
    assert "TCS" in idn.aliases


# 1. company-specific query generation -------------------------------------
def test_query_generation_is_company_scoped():
    idn = CN.resolve_identity("ITC Limited", "ITC")
    qs = CN.generate_queries(idn)
    assert 6 <= len(qs) <= 12
    assert all('"ITC"' in q or "ITC" in q for q, _ in qs)
    joined = " ".join(q for q, _ in qs).lower()
    for dim in ("earnings", "capex", "acquisition", "regulation", "management"):
        assert dim in joined


# 5. company relevance filtering -------------------------------------------
def test_relevance_filters_generic_news():
    idn = CN.resolve_identity("ITC Limited", "ITC")
    on = _raw("ITC Q1 net profit rises 8% on cigarettes strength", "Reuters", "u1")
    generic = _raw("Nifty ends 200 points higher led by banks", "Economic Times", "u2")
    assert CN.company_relevance(on, idn) >= 0.6
    assert CN.company_relevance(generic, idn) == 0.0


# 6. source validation ------------------------------------------------------
def test_source_validation():
    good = _raw("ITC announces hotel demerger", "Business Standard",
                "https://www.business-standard.com/x")
    bad = _raw("ITC to double capex, says blog", "RandomFinanceBlog",
               "https://randomfinanceblog.co/x")
    assert CN.is_trusted(good) is True
    assert CN.is_trusted(bad) is False
    assert CN.source_quality(good) >= 0.8
    assert CN.source_quality(bad) == 0.0


# 7 + 8. duplicate + paraphrase detection ----------------------------------
def test_dedup_same_event_and_paraphrase():
    idn = CN.resolve_identity("ITC Limited", "ITC")
    raw = [
        _raw("ITC Q1 profit rises 8%", "Reuters", "r", days=2),
        _raw("ITC profit jumps in first quarter", "Economic Times", "e", days=2),
        _raw("ITC Q1 net profit up 8% YoY", "Moneycontrol", "m", days=2),
        _raw("ITC plans major hotel expansion", "Business Standard", "b", days=10),
        _raw("ITC Hotels outlines aggressive expansion strategy", "Mint", "n", days=11),
    ]
    for it in raw:
        it["category"] = CN.classify_category(it)
        it["relevance_score"] = CN.company_relevance(it, idn)
        it["source_quality"] = CN.source_quality(it)
    out = CN.dedup(raw, idn)
    titles = " || ".join(o["title"] for o in out)
    # the three earnings paraphrases collapse to one; the two hotel ones to one
    assert len(out) == 2, titles


# 9. ranking prefers material strategic over trivial recent ----------------
def test_ranking_materiality():
    idn = CN.resolve_identity("ITC Limited", "ITC")
    strategic = _raw("ITC to acquire stake in packaging firm", "Reuters", "r", days=25)
    trivial = _raw("ITC shares edge higher in early trade", "Moneycontrol", "m", days=1)
    items = []
    for it in (trivial, strategic):
        it["category"] = CN.classify_category(it)
        it["relevance_score"] = CN.company_relevance(it, idn)
        it["source_quality"] = CN.source_quality(it)
        it["horizon"] = CN.classify_horizon(it, it["category"])
        items.append(it)
    ranked = CN.rank_and_select(items)
    assert ranked[0]["title"].startswith("ITC to acquire")


# 10. horizon classification ------------------------------------------------
def test_horizon_classification():
    capex = _raw("ITC to invest 20000 crore in new capacity expansion", "BS", "u")
    earn = _raw("ITC Q1 results: net profit rises", "Reuters", "u2")
    reg = _raw("SEBI issues new disclosure regulation affecting ITC", "Mint", "u3")
    assert CN.classify_horizon(capex) == "LONG TERM"
    assert CN.classify_horizon(earn) == "SHORT TERM"
    assert CN.classify_horizon(reg) == "ONGOING"


# 11. maximum 12 articles ---------------------------------------------------
def test_max_twelve():
    idn = CN.resolve_identity("ITC Limited", "ITC")
    raw = [_raw(f"ITC development number {i} in strategy and orders", "Reuters",
                f"u{i}", desc=f"distinct event {i}", days=i % 30 + 1)
           for i in range(25)]
    items = CN.build_items(raw, idn, config=None)
    assert len(items) <= 12


# 12 + 13. UI initial 3 vs expand ------------------------------------------
def _entry_with(n):
    idn = CN.resolve_identity("ITC Limited", "ITC")
    raw = [_raw(f"ITC {cat} update {i}", "Reuters", f"http://x/{i}",
                desc=f"real blurb about event {i}", days=i + 1)
           for i, cat in enumerate(["capex", "acquires", "results", "management",
                                     "regulation", "order", "demand", "strategy"][:n])]
    items = CN.build_items(raw, idn, config=None)
    return {"company": "ITC", "items": [it.as_public() for it in items],
            "updated_display": "14 Sep 2026", "empty": False}


def test_ui_initial_three_and_expand():
    entry = _entry_with(8)
    n = len(entry["items"])
    assert n >= 4
    html = CN.render_section(entry)
    # every item is rendered once; only 3 are visible, the rest carry cn-hidden
    assert html.count('class="nc"') == 3                      # visible cards
    assert html.count('class="nc cn-hidden"') == n - 3        # the rest hidden
    # a JS 'See all' toggle reveals the hidden cards (no network)
    assert "cnToggle" in html and f"See all {n} developments" in html


# 14 + 15. each card links to its own article; no separate Sources section ---
def test_card_links_and_no_sources_section():
    entry = _entry_with(6)
    n = len(entry["items"])
    html = CN.render_section(entry)
    assert 'class="cn-src"' not in html               # the Sources strip is gone
    assert "cn-src-l" not in html                     # no "SOURCES" label either
    # every card (visible or hidden) links to its own article in its footer
    assert html.count('<a class="nc-src"') == n
    assert 'href="http' in html


# 16. missing news does not crash ------------------------------------------
def test_missing_news_no_crash(monkeypatch):
    monkeypatch.setattr(CN, "_fetch", lambda *a, **k: [])   # simulate no results
    entry = CN.get_company_news("Some Unknown Co", "ZZZZZ", config=None, force=True)
    assert entry["empty"] is True and entry["items"] == []
    html = CN.render_section(entry)
    assert "No recent company-specific developments" in html


# 17. LLM failure does not fabricate ---------------------------------------
def test_llm_failure_uses_deterministic_summary():
    idn = CN.resolve_identity("ITC Limited", "ITC")
    raw = [_raw("ITC commissions new paperboard line",
                "Business Standard", "u",
                desc="ITC has commissioned a new paperboard manufacturing line at its unit.")]
    items = CN.build_items(raw, idn, config=None)      # no LLM
    assert len(items) == 1
    s = items[0].summary.lower()
    assert "unavailable" not in s and "error" not in s
    assert "paperboard" in s                            # grounded in the real blurb


# 18. existing sector news untouched ----------------------------------------
def test_sector_news_module_intact():
    from core import market_context as MC
    # company_news borrows read-only helpers; it must not mutate them
    assert callable(MC.get_context)
    assert isinstance(MC._TRUSTED, tuple) and "reuters" in MC._TRUSTED
    assert MC._MAX_AGE_DAYS == 183                       # sector window unchanged
