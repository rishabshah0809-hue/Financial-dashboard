# FundaCheck

**An AI-assisted fundamental analysis terminal.** Upload a 3-statement Excel
model, pick the company's sector, and get an interactive dashboard plus a
**STRONG / NEUTRAL / WEAK** verdict — judged against *sector-specific*
benchmarks rather than one universal rule book.

![Python](https://img.shields.io/badge/Python-3.10%2B-1e6b45)
![Streamlit](https://img.shields.io/badge/Streamlit-app-37d67a)
![License](https://img.shields.io/badge/license-MIT-777)

---

## The problem it solves

Reading a 3-statement model means holding forty ratios in your head at once and
— the harder part — remembering that each one means something different
depending on the industry:

| Ratio | Software company | Bank | Infrastructure developer |
|---|---|---|---|
| Debt / equity of 8x | solvency alarm | completely normal | over-levered |
| ROCE of 12% | disappointing | not a meaningful metric | respectable |
| 130-day working capital cycle | broken collections | not applicable | business as usual |

A single scorecard applied to every company produces confident nonsense.
FundaCheck keeps one rule book per sector and applies the right one.

## What it does

1. **Parses** any Screener.in-style 3-statement workbook — income statement,
   balance sheet, cash flow, ratio analysis and common-size sheets.
2. **Scores** twelve key ratios against that sector's weak/strong bands, rolls
   them into five pillars (growth, profitability, returns, leverage,
   efficiency) and weights those pillars by what the sector actually rewards.
3. **Visualises** everything as an interactive dark-terminal dashboard.
4. **Explains** the result through a free LLM writing a sector-aware analyst
   note — and answers follow-up questions about the loaded company.

### The core idea, made visible

The **Sector lens** tab runs all nine sector rule books over the same company at
once. The financials never change; only the yardstick does — and the verdict
moves with it. That single chart is the whole thesis of the project.

## Screens

The sidebar is permanent — the upload, the sector lens, the AI settings and the
day/night switch stay on screen on every page, and the collapse control is
removed so the nav can't be dismissed.

| Page | What's in it |
|---|---|
| **Dashboard** | Ten headline ratios with their sector verdict, the composite score ring, the analyst note, and a bento grid of ten charts — one per ratio, each in the form that ratio needs |
| **Ratio deep dive** | Every ratio scored 0-100 against its sector band, leverage & solvency, working-capital cycle, and any ratio plotted through time with the sector bands shaded |
| **Sector lens** | The same company scored under all nine sector rule books, plus a ratio correlation matrix |
| **Statements** | The parsed sheets as heat-shaded tables, exportable to CSV |
| **Ask the analyst** | Free-text Q&A grounded strictly in the loaded model |

## Getting started

```bash
git clone <your-repo-url>
cd Financial-dashboard

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
streamlit run app.py
```

The app opens at `http://localhost:8501`. A real 3-statement model ships in
`sample_data/`, so it is usable the moment it starts — no upload needed.

### Connecting the AI analyst

The analyst runs on Groq's free tier with a **reasoning** model
(`openai/gpt-oss-120b` at high reasoning effort) — the verdict is a judgement
across a dozen interacting ratios, which is where a model that thinks before
answering earns its place.

Keys live in the deployment's secret store, never in the UI and never in this
repository. Copy the example and fill in your own:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# then edit it — the file is gitignored
```

```toml
[groq]
api_keys = ["gsk_first_key", "gsk_second_key"]
```

Two or more keys are supported: free tiers rate-limit per key, so the app moves
to the next one instead of dropping to its offline note. `GROQ_API_KEYS`
(comma-separated) works too. On Streamlit Cloud, paste the same block into
**Settings → Secrets**.

With no keys configured the app still works end to end — the commentary falls
back to a deterministic, rule-based note.

## How the score is built

```
raw ratio ──► sector band ──► 0-100 sub-score ──► pillar ──► weighted total ──► verdict
              (weak/strong)    60% latest year        (5)      (sector weights)   STRONG / NEUTRAL / WEAK
                               40% 3-year average
                               ± trend adjustment
```

Design decisions worth defending in an interview:

- **60/40 latest vs 3-year average.** One good year can be luck; three years is
  character. Neither alone is enough.
- **Linear scaling between the bands**, not a pass/fail cutoff, so a company
  just short of "strong" is not lumped in with one in real trouble.
- **A trend adjustment (±6 points)** so a deteriorating 15% ROCE scores below an
  improving one.
- **An earnings-quality override.** If three-year average CFO/PAT is below 0.5 —
  profit that never becomes cash — the total is docked five points regardless of
  how good the margins look.
- **The LLM never touches a number.** It receives the finished scorecard and
  writes the explanation, so every figure on screen is traceable to the
  workbook. That ordering is deliberate and is the honest way to put an LLM in a
  finance tool.

Thresholds live in `core/sectors.py` and are ordinary Python dictionaries —
adding a sector or tuning a band is a few lines, no code changes elsewhere.

## Design notes

The chart layer follows a few rules that are worth knowing, because they are the
difference between "looks like a dashboard" and "can be trusted":

- **No dual-axis charts.** Revenue-vs-margin and leverage-vs-cover used to be
  single charts with a second y-scale. Two scales let you imply any relationship
  you like by choosing the ranges, so both are now stacked small multiples on a
  shared x-axis — same story, no manufactured crossover.
- **Colour is assigned by the job it does.** Identity gets the fixed categorical
  order (never cycled, never reassigned by rank); the common-size stack gets one
  hue dark-to-light because it is magnitude; growth and correlation grids get a
  diverging pair with a **gray** midpoint because they have polarity. Status
  colours (good/warning/critical) are reserved and never double as a series.
- **The palette was validated, not eyeballed.** The five categorical slots were
  checked against the dark surface for lightness band, chroma floor,
  colourblind separation and 3:1 contrast. All five pass on the adjacent
  pairlist that bars, stacks and lines use; forms that compare every pair at
  once are capped at three slots and always direct-labelled.
- **Identity never rests on colour alone** — every multi-series chart carries a
  legend, key points are direct-labelled, and the tables carry the same numbers.
- **One chart form per ratio, chosen by the data's job.** Returns get a banded
  area against the sector threshold; net margin gets a profit ladder; EBITDA
  margin gets a bullet against its target band; debt gets a funding-mix area;
  interest cover gets a shaded danger zone; growth gets diverging columns;
  cash quality gets a dumbbell, because the gap between profit and cash *is*
  the question; valuation gets a strip against the company's own median.
- **A workbook that only contains formulas still analyses.** Derived sheets
  often carry no cached values, so they read as empty. The statements are
  rebuilt from the raw Data Sheet and any missing benchmark ratio is computed
  from them — the workbook's own numbers always win where it supplies them.
- **The sector follows the company.** It is detected from the workbook — name
  first, balance-sheet shape as a fallback — and the sidebar says which signal
  decided it. The dropdown still overrides.
- **Small multiples wherever one scale would lie.** The cost structure used to
  be a 100% stacked bar, but COGS is 75-90% of sales, so everything else was an
  invisible sliver. Each line now gets its own panel and its own y-scale.
  Cash flow got the same treatment for the same reason.
- **Both themes are selected, not flipped.** The light palette's five series
  colours were validated against the light surface on their own; inverting the
  dark set would have failed the lightness band.
- **Motion respects `prefers-reduced-motion`.** The entrance animations, the
  score ring sweep and the meter fills all collapse to nothing for anyone who
  has asked their OS for less movement.

## Project layout

```
app.py               Streamlit UI — layout, tabs, and nothing else
core/
  parser.py          Excel → clean DataFrames (layout-tolerant)
  sectors.py         Nine sector rule books: bands, weights, context notes
  scoring.py         Ratio → sub-score → pillar → verdict engine
  llm.py             Free LLM clients (Groq / OpenRouter) + offline fallback
  charts.py          Every Plotly figure, one house style
assets/style.css     Terminal theme
sample_data/         A real 3-statement model to demo with
```

The parser makes no assumption about which rows exist. It finds the row labelled
`Year`, treats `#` in the left margin as a section break, and reads everything
else as a metric — which is why a workbook with extra or missing rows still
loads. Summary columns (`Mean`, `Median`, `CAGR`) are detected and excluded so
they never contaminate a time series.

## Limitations (stated honestly)

- Sector bands are calibrated from general Indian large-cap norms, not from a
  live peer database. They are a defensible starting point, not gospel.
- Banks and NBFCs are scored on a reduced metric set — turnover and
  working-capital ratios are meaningless for lenders, so they are down-weighted
  rather than reinterpreted.
- The verdict is a screening aid. It is not investment advice, and it cannot see
  management quality, governance, or anything outside the workbook.

## Quarterly results PDF ingestion

FundaCheck also accepts a listed Indian company's **quarterly-results PDF** as an
additional input mode, alongside the existing Excel/annual workflow (which is
unchanged). Upload a PDF in the sidebar and it is auto-detected.

Pipeline (deterministic; the LLM never calculates a number):

```
Quarterly-results PDF
  → document / section detection   (consolidated preferred; standalone rejected)
  → native PDF text + geometry     (pdfplumber; tables via row/column alignment)
  → targeted OCR only where needed (rasterised / scanned tables)
  → period detection               (current + immediately previous quarter only)
  → label & unit normalisation     (₹ crore / lakh / million → normalised)
  → Python deterministic ratios
  → validation vs the PDF's own reported ratios
  → Screener fallback ONLY for a genuinely missing quarterly value
  → provenance-tagged quarterly dataset → charts / scoring / analyst text
```

- **Exactly two quarters** (current + previous) are used; annual / TTM / year-ago
  and nine-month columns are detected and excluded from the quarterly dataset.
- **OCR engines (optional):** PaddleOCR PP-StructureV3 (primary) with Surya as a
  secondary cross-check, targeted only at pages/regions that need it. **Neither
  is installed on the Streamlit Community Cloud demo** — `paddlepaddle` has no
  wheel for the Cloud's Python and both are too heavy for its image limits — so
  the app **degrades gracefully**: native-text filings work fully, and
  rasterised/scanned cells are marked unavailable (never fabricated). Install
  `paddlepaddle`+`paddleocr` (and optionally `surya-ocr`) in a self-hosted
  environment to enable OCR of scanned/image tables. See `requirements.txt`.
- **Provenance:** every value is tagged `pdf_raw`, `python_derived`,
  `pdf_reported_validation`, `screener_fallback`, or `unavailable`. Missing data
  is shown as "—", never fabricated, and an annual/TTM Screener value is never
  presented as a quarterly value.

## Roadmap

- [ ] Peer comparison — load several models and rank them side by side
- [ ] Auto-detect the sector from the revenue mix instead of asking
- [ ] Export the analyst note as a formatted PDF tearsheet
- [ ] Altman Z-score and Piotroski F-score alongside the composite
- [ ] A light theme (the dark palette would need re-validating against a light surface, not just flipped)

---

MIT licensed. Built as a portfolio project — issues and forks welcome.

---

**Repository:** https://github.com/rishabshah0809-hue/Financial-dashboard

**Quarterly Data branch:** `quaterly-data`

**Quarterly Data website:** https://fundacheck-quaterly.streamlit.app

Quarterly PDF development is isolated to the `quaterly-data` branch. The `main`
branch is not modified by this workflow.
