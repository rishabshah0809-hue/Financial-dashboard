# Sector Lens → Niche Industries — TODO

Branch: `feature/sector-lens-niche-industries` (off `main`). Do NOT touch/commit `main`.
Rule: apply changes to folder + localhost:8501, do NOT commit until user says so.

## Phase 0 — Setup
- [x] Create feature branch off current main state
- [x] Save workflow prefs to memory
- [x] Create this TODO

## Phase 1 — REQUIRED REPORT (before any coding) — Section 19
- [x] A. Current sector mapping files/functions
- [x] B. Current constituent source
- [x] C. Current Top-10 logic
- [x] D. Current sector aggregation logic
- [x] E. Current monthly snapshot structure
- [x] F. Files needing modification
- [x] G. Mapping for all 25 sectors → ALL are official NSE Nifty sectoral indices (EXACT). NSE launched 11 new ones Jun-2026 + Cement Feb-2026 + Chemicals Mar-2026.
- [x] H. Total UNIQUE companies = **409** (all 25 CSVs resolved; gross slots 472)
- [x] I. IndianAPI requests = 409/month (one per unique company)
- [x] J. Fits a fresh 500 month (409<500) BUT >400 guard (raise to 460). BLOCKER: ~390 credits already spent this month this session -> only ~100 left -> defer full live refresh to Oct-1 auto-run or a confirmed higher balance. Raise guard 400->460.
- [x] K. Unavailable sectors — NONE (all 25 authoritative)
- [x] L. Adani Enterprises → NSE "Diversified"; not in any niche index → mark Unclassified (no niche peer set)
- [ ] **STOP for user review — do not code until approved**  ← WE ARE HERE

## Phase 2 — Implementation (APPROVED — credits confirmed)
- [x] Build authoritative 25-niche + 2-fallback universe (sector_universe.py; dedup by symbol)
- [x] Primary-business classification (all 13 test companies pass; Adani->Metal)
- [x] Remove Top-10 truncation -> ALL constituents (rank_top n=None; field renamed constituents)
- [x] Peer table columns already present; heading "Peer comparison — all N constituents"; scrollable
- [x] Sector benchmark cards from niche universe (formulas preserved)
- [x] Company-vs-Sector wired to niche via classify_symbol + manual override; 27 per-sector cycle texts
- [x] MoM vs previous snapshot (key renamed constituents)
- [x] Credit guard raised 400->460; dedup fetch; previous snapshot preserved on failure
- [x] Removed old broad universes + orphan CSVs (one source of truth)
- [x] Added 429 backoff+retry, pacing 0.7s (per-minute rate limit hit on first 410 burst)
- [~] Live refresh (410 companies) running in background after 429 abort
- [ ] Keep snapshot architecture + monthly workflow; NO Trendlyne; NO fake data (verify)

## Phase 3 — Validation & final report
- [ ] Validate 13 companies (symbol, primary industry, target sector, membership, correct peer set)
- [ ] Confirm no Top-10 truncation, dedup fetch, NSE links, "—" for missing, financial ROCE "—"
- [ ] Backward compat: Dashboard/Brief/Ratio/Statements/scoring/search unaffected
- [ ] Update localhost:8501, give link
- [ ] Final report (files changed, counts, API estimate, examples, tests, diff)
