# FundaCheck

**Turn a company's financial spreadsheet into an equity‑research snapshot.**

You upload a company's 3‑statement Excel model (income statement, balance sheet,
cash flow — the kind you download from Screener.in). FundaCheck reads it, judges
it against the *right yardstick for that industry*, pulls in what's happening
around the company right now, and hands you back a plain‑English read: is this
business **STRONG, NEUTRAL, or WEAK**, and why.

![Python](https://img.shields.io/badge/Python-3.12-1e6b45)
![Streamlit](https://img.shields.io/badge/Streamlit-app-37d67a)
![License](https://img.shields.io/badge/license-MIT-777)

Live app → **https://fundacheck.streamlit.app**

---

## Why it exists (the one idea)

The same number means completely different things in different industries:

| A ratio of… | For a software company | For a bank | For an infrastructure builder |
|---|---|---|---|
| Debt ÷ equity of 8× | solvency alarm 🚨 | completely normal | over‑borrowed |
| Return on capital of 12% | disappointing | not even a meaningful metric | respectable |
| A 130‑day cash cycle | broken collections | doesn't apply | business as usual |

Judge every company with one rulebook and you get confident nonsense.
**FundaCheck keeps a separate rulebook per sector and always applies the right one.**

---

## How it works — eight layers

Think of it like an analyst who checks the company from the inside out. Each
layer builds on the one before it. You don't do any of this by hand — you upload
one file and every layer fills itself in.

### Layer 1 · Financial statements
It reads your Excel workbook — however messy — and lays out the **income
statement, balance sheet, cash flow and common‑size** views as clean, colour‑shaded
tables. If the sheet only has formulas (no saved values), it rebuilds the numbers
from the raw data sheet, so nothing shows up blank.
*You see:* the company's actual financials, tidied up.

### Layer 2 · Ratios
It computes and scores the key ratios — margins, returns (ROE/ROCE/ROA),
leverage, interest cover, working‑capital cycle, growth — and gives each one a
**0–100 score against that sector's weak/strong band**, then rolls them into a
single **Funda Score** and a STRONG/NEUTRAL/WEAK verdict.
*You see:* every ratio scored and charted through time, and a headline score with
a tap‑to‑open breakdown of how it was built.

### Layer 3 · Sector comparison
It finds the company's real peers — the members of its **NIFTY sectoral index** —
and compares it against the whole basket: how it's priced (P/E, P/B), how much it
earns on capital, how it's growing, and where it sits on a "cheap vs. expensive /
earning more vs. less" map. Peer numbers come live from Screener.in.
*You see:* the company as a dot among its peers, plus the sector's own vitals and
a **seasonality heatmap** (which months the sector's index has historically been
strong or weak).

### Layer 4 · Company news
It pulls **recent, trusted news about this specific company** — results,
acquisitions, capex, management changes, orders, regulation — from credible
financial sources only (Reuters, Business Standard, Economic Times, Mint,
Moneycontrol, exchange filings…). It removes duplicate versions of the same
story, tags each item by type and by time horizon (short / medium / long term),
and shows the 3 most important, with a "see all" to reveal the rest.
*You see:* what's actually happening with the company, not a generic market feed.
It refreshes on its own roughly every 6 hours.

### Layer 5 · Market & sector context
Above the company sits its world. This layer reads the **global drivers** (the
commodity, rate and demand forces that move the sector) and the **Indian
read‑through** (how those reach domestic producers), and places the sector on its
**economic cycle** — early, mid, late, or slowing.
*You see:* "Where the cycle sits now" — the outside forces that could matter over
the next quarter and year, refreshed every ~6 hours.

### Layer 6 · Valuation
It puts the company's valuation next to its sector: is it paying a **premium or a
discount** on earnings and book value, and is that gap justified by higher (or
lower) returns and growth?
*You see:* a "what you pay vs. what you get" panel and a valuation read in plain
words.

### Layer 7 · Research interpretation
This is the analyst's write‑up. A free AI model takes the **finished scorecard**
(it never touches a number itself) and writes a sector‑aware read across seven
areas — growth, margins, costs, returns, efficiency, leverage & cash, valuation —
each with the verdict it earns on its own evidence. You can also **ask it
questions** in plain English, and it answers only from the loaded company's data.
*You see:* seven verdict cards and a chat box that behaves like a junior analyst
who has read only this company's file.

### Layer 8 · Research memo
One click exports everything above as a **multi‑page equity‑research PDF** — a
tearsheet you could hand to someone: contents, the verdict, a revenue‑to‑profit
flow, the full scorecard, the sector comparison, the statements, the
interpretation, valuation, catalysts & risks, and a page explaining the method.
*You get:* a shareable research document, generated in seconds.

---

## What you end up with: an Equity Research Snapshot

Pick a company, and FundaCheck assembles something that reads like a professional
one‑pager:

- **Business & verdict** — the headline STRONG / NEUTRAL / WEAK call and Funda Score
- **Financials** — income statement and balance sheet, tidied and trended
- **Quality** — every ratio scored against its sector band
- **Valuation** — priced above or below the sector, and whether that's earned
- **Sector** — peer comparison and seasonality
- **Catalysts & risks** — what could push the story either way
- **Recent developments** — the live company news
- **Research interpretation** — the plain‑English analyst read

---

## Getting started

```bash
git clone https://github.com/rishabshah0809-hue/Financial-dashboard
cd Financial-dashboard

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
streamlit run app.py
```

The app opens at `http://localhost:8501`. A real 3‑statement model ships in
`sample_data/`, and there's a **Load demo model** button — so it works the moment
it starts, no upload needed.

### Connecting the AI

The written interpretation and the "Ask the analyst" chat run on free tiers of
**Groq** (a reasoning model) with **Google Gemini** as an automatic backup, so if
one is rate‑limited the other answers. Keys live in the deployment's secret store,
never in the UI or this repository:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # gitignored
```

With **no keys**, the app still works end to end — the written read falls back to
a deterministic, rule‑based note, and the news falls back to real headlines
without AI summaries. It never shows an error or invents anything.

---

## How the Funda Score is built

```
raw ratio ─► sector band ─► 0–100 sub‑score ─► pillar ─► sector‑weighted total ─► verdict
            (weak/strong)   60% latest year      (5)      (weights per sector)     STRONG / NEUTRAL / WEAK
                            40% 3‑year average
                            ± trend adjustment
```

The principles it sticks to:

- **60/40 latest vs. 3‑year average** — one good year can be luck; three years is character.
- **Smooth scaling, not pass/fail** — a company just short of "strong" isn't lumped in with one in trouble.
- **A trend nudge** — a deteriorating 15% return scores below an improving one.
- **An earnings‑quality check** — if profit isn't turning into cash, the score is docked, however pretty the margins.
- **The AI never touches a number.** It receives the finished, traceable scorecard and only writes the words. Every figure on screen comes from your workbook or a named source — nothing is invented.

Sector thresholds live in `core/sectors.py` as plain Python dictionaries — tuning
a band or adding a sector is a few lines.

---

## Honest limitations

- Sector bands are calibrated from general Indian large‑cap norms, not a live peer database — a defensible starting point, not gospel.
- Banks and lenders are scored on a reduced metric set (turnover and working‑capital ratios are meaningless for them).
- Some sector seasonality is **reconstructed from constituent stocks** (equal‑weighted) where the official index history isn't available — clearly labelled as such.
- FundaCheck is a **screening aid, not investment advice.** It can't see governance, and it only knows what's in the workbook and the public news it retrieves.

---

## On the way

- [ ] Load several companies and rank them side by side
- [ ] Auto‑detect the sector from the revenue mix instead of asking

---

MIT licensed. Built as a portfolio project — issues and forks welcome.
