"""
shell.py
--------
Renders each page as ONE self-contained HTML document that mirrors the
FundaCheck reference page exactly: gradient backdrop, floating #fbfbfa shell,
top bar, hero, KPI grid, verdict card with drivers inside it, tinted
strengths/risks panels with a working See-all toggle, and every panel in the
reference order. Python computes all numbers; small vanilla-JS handlers give
the page its interactions (search filter, statements tabs, % change toggle,
peer add, See-all).

Chart fragments come from core.viz / core.sections and share one tooltip
engine inside the shell.
"""

from __future__ import annotations

import base64
from html import escape

import pandas as pd

from . import design_blocks as D
from . import sections as S
from . import viz
from .scoring import Assessment

INK = viz.INK
BODY = viz.BODY
MUTED = viz.MUTED
FAINT = viz.FAINT
GREEN = viz.GREEN
AMBER_TXT = viz.AMBER_TXT
GREEN_DARK = "#0f5b34"
MONO = viz.MONO


# ==========================================================================
# stylesheet - transcribed from the reference page's inline styles
# ==========================================================================
SHELL_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
/* html fills any frame overshoot below the (now content-height) body with the
   page's end colour, so a frame taller than its content shows plain page space
   rather than a giant stretched card. color-scheme:light stops a viewer whose
   browser is in dark mode from getting a dark UA canvas behind the page. */
html{background:#d9ded9;color-scheme:light}
body{margin:0;padding:6px 20px 14px;
  background:linear-gradient(180deg,#e7ebe7,#d9ded9);
  font-family:'Plus Jakarta Sans',system-ui,sans-serif;color:#15201a;
  display:flex;justify-content:center;align-items:flex-start}
#shell{width:1240px;max-width:100%;background:#e9ece8;border-radius:24px;
  padding:18px;display:flex;gap:20px;box-shadow:0 10px 30px rgba(21,32,26,.06)}
main{flex:1;min-width:0;display:flex;flex-direction:column;gap:18px}
a{color:#177245;text-decoration:none}
svg{display:block;width:100%;height:auto;overflow:visible}
text{font-family:'Plus Jakarta Sans',system-ui,sans-serif}
.mono{font-family:ui-monospace,Menlo,monospace}

/* ---- hover animation: cards lift with a deeper shadow ---- */
.card,.kpi,.verdict,.strip,.herostat{
  transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease}
.card:hover,.strip:hover{transform:translateY(-3px);
  box-shadow:0 2px 4px rgba(21,32,26,.05),0 14px 34px rgba(21,32,26,.12);
  border-color:#d7e0d9}
.kpi:hover{transform:translateY(-3px);
  box-shadow:0 2px 4px rgba(21,32,26,.05),0 14px 30px rgba(21,32,26,.13)}
.kpi.score:hover{box-shadow:0 14px 34px rgba(15,74,44,.30)}
.verdict:hover{transform:translateY(-2px);
  box-shadow:0 2px 4px rgba(21,32,26,.05),0 14px 30px rgba(21,32,26,.11)}
.exportbtn{transition:transform .16s ease,box-shadow .16s ease,background .16s ease}
.exportbtn:hover{transform:translateY(-1px);
  box-shadow:0 6px 16px rgba(21,32,26,.14)}
.aibtn{transition:transform .16s ease,box-shadow .16s ease}
.aibtn:hover{transform:translateY(-1px);box-shadow:0 8px 20px rgba(13,74,44,.28)}

/* ---- top bar ---- */
.topbar{background:#fff;border-radius:20px;padding:14px 18px;border:1px solid #e6ebe7;box-shadow:0 1px 2px rgba(21,32,26,.04),0 6px 18px rgba(21,32,26,.06);display:flex;
  align-items:center;gap:16px;flex-wrap:wrap}
.searchpill{flex:1 1 200px;min-width:180px;max-width:330px;display:flex;
  align-items:center;gap:11px;background:#f5f6f5;border-radius:14px;
  padding:11px 15px}
.searchpill input{border:none;background:transparent;outline:none;flex:1;
  min-width:0;font-size:14.5px;font-family:inherit;color:#15201a}
.searchpill input::placeholder{color:#9aa09d}
.searchpill .lens{width:13px;height:13px;border-radius:50%;
  border:2px solid #9aa09d;flex:none}
.searchpill .kbd{font-size:11.5px;font-weight:600;color:#8d938f;
  background:#fff;border-radius:7px;padding:4px 8px}
.topright{margin-left:auto;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.monitor{width:44px;height:44px;border-radius:50%;background:#f5f6f5;
  display:flex;align-items:center;justify-content:center}
.monitor i{width:16px;height:12px;border:2px solid #4a5350;border-radius:3px}
.aibtn{display:flex;align-items:center;gap:11px;padding:7px 18px 7px 8px;
  border-radius:30px;cursor:pointer;border:none;
  background:radial-gradient(130% 130% at 10% 0%,#2a9c62,#0d4a2c)}
.aibtn .ic{width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.16);
  display:flex;align-items:center;justify-content:center;font-size:16px;color:#fff}
.aibtn .t1{font-size:14.5px;font-weight:700;color:#fff;line-height:1.2;text-align:left}
.aibtn .t2{font-size:10px;letter-spacing:1.2px;color:rgba(255,255,255,.62);
  font-family:ui-monospace,Menlo,monospace}

/* ---- hero ---- */
.hero{background:#eef2ee;border-radius:22px;padding:24px;display:flex;
  align-items:flex-start;gap:20px;flex-wrap:wrap}
.hero h1{font-size:38px;font-weight:800;letter-spacing:-1.2px;color:#15201a;
  line-height:1.1}
.herosub{display:flex;align-items:center;gap:10px;padding-top:8px;flex-wrap:wrap}
.ticker{font-family:ui-monospace,Menlo,monospace;font-size:12px;letter-spacing:1px;
  color:#8b918e}
.dotsep{color:#cdd2cf}
.heroright{margin-left:auto;display:flex;align-items:center;gap:12px;
  flex-wrap:wrap;justify-content:flex-end;flex:1 1 auto;min-width:0}
.herostat{background:#fff;border-radius:20px;padding:13px 22px;display:flex;
  align-items:center;gap:16px;flex-wrap:wrap;min-width:0}
.herostat .lbl{font-family:ui-monospace,Menlo,monospace;font-size:9.5px;
  letter-spacing:1.3px;color:#a4a9a6}
.herostat .val{font-size:26px;font-weight:800;letter-spacing:-1px;color:#15201a}
.herostat .val small{font-size:15px;color:#8b918e}
.herostat .up{font-size:11.5px;font-weight:700;color:#177245;background:#eef4f0;
  border-radius:7px;padding:3px 7px}
.vrule{width:1px;height:38px;background:#eceeec}
.mcap{font-size:15px;font-weight:700;color:#15201a;padding-top:6px}
.exportbtn{background:#fff;color:#15201a;border:1.5px solid #177245;
  border-radius:26px;padding:15px 26px;font-size:14.5px;font-weight:700;
  cursor:pointer;font-family:inherit}

/* ---- KPI grid ---- */
.kpigrid{display:grid;grid-template-columns:repeat(auto-fit,
  minmax(min(220px,100%),1fr));gap:14px;align-items:stretch}
/* flex column + ft margin-top:auto keeps the big numbers and footers aligned
   across all four cards so the row reads as one even set of boxes. */
.kpi{border-radius:18px;padding:20px 22px;background:#fff;border:1px solid #e6ebe7;box-shadow:0 1px 2px rgba(21,32,26,.04),0 6px 18px rgba(21,32,26,.06);
  display:flex;flex-direction:column;min-height:172px}
.kpi .hd{display:flex;align-items:center;justify-content:space-between}
.kpi .ft{margin-top:auto}
.kpi .name{font-size:15px;font-weight:600;color:#15201a}
.kpi .circ{width:30px;height:30px;border-radius:50%;border:1.5px solid #dcdfdc;
  display:flex;align-items:center;justify-content:center;font-size:13px;
  color:#4a5350}
.kpi.score{color:#fff;background:radial-gradient(130% 130% at 85% 15%,#2a9c62 0%,
  #177245 45%,#0d4a2c 100%)}
.kpi.score .circ{border-color:rgba(255,255,255,.5);color:#fff}
.kpi.score .name{color:#fff}
.kpi.score .ft{color:rgba(255,255,255,.92)}
.kpi.score .chip{background:rgba(255,255,255,.28);color:#fff}
.kpi .big{font-size:44px;font-weight:800;letter-spacing:-1.5px;color:#15201a;
  padding:14px 0 12px}
.kpi.score .big{color:#fff}
.kpi .big small{font-size:20px;font-weight:600;opacity:.7}
.kpi .ft{display:flex;align-items:center;gap:8px;font-size:12.5px;color:#8b918e}
.kpi .chip{border-radius:6px;padding:3px 6px;font-weight:700}
.chip.g{background:#eef4f0;color:#177245}
.chip.r{background:#fbeeec;color:#b4483c}
.chip.w{background:#fdf3e2;color:#b5761f}
.kpi.score .chip{background:rgba(255,255,255,.18);color:#fff}

/* ---- verdict card ---- */
.verdict{background:#fff;border-radius:18px;padding:24px 26px;border:1px solid #e6ebe7;box-shadow:0 1px 2px rgba(21,32,26,.04),0 6px 18px rgba(21,32,26,.06);
  border-left:4px solid var(--rail,#d9a441);display:grid;
  grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr));
  gap:28px;align-items:center}
.verdict .chips{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.verdict .vtag{font-family:ui-monospace,Menlo,monospace;font-size:10.5px;
  font-weight:700;letter-spacing:1.4px;color:#b5761f;background:#fdf3e2;
  border-radius:20px;padding:7px 13px}
.verdict .sect{font-family:ui-monospace,Menlo,monospace;font-size:10px;
  letter-spacing:1.2px;color:#a4a9a6}
.verdict h2{font-size:29px;font-weight:800;letter-spacing:-.9px;color:#15201a;
  padding:16px 0 12px}
.verdict p{font-size:14.5px;line-height:1.65;color:#5f6663;text-wrap:pretty}
.drivers{display:flex;flex-direction:column;gap:10px}
.drivers .drow .dl{display:flex;justify-content:space-between;font-size:13px;
  color:#3f4744;padding-bottom:5px}
.drivers .drow b{font-weight:700}
.drivers .track{height:7px;border-radius:7px;background:#f1f3f1}
.drivers .fill{height:100%;border-radius:7px}

/* ---- strengths / risks ---- */
.srgrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;
  align-items:start}
.srpanel{border-radius:18px;padding:20px}
.srpanel.str{background:#f0f7f3}
.srpanel.rsk{background:#fcf1ef}
.srhead{display:flex;align-items:center;justify-content:space-between;
  padding:0 4px 16px}
.srhead .t{font-size:17px;font-weight:700;color:#15201a}
.srhead .cnt{width:28px;height:28px;border-radius:50%;color:#fff;font-size:12.5px;
  font-weight:700;display:flex;align-items:center;justify-content:center}
.srlist{display:flex;flex-direction:column;gap:10px}
.sritem{background:#fff;border-radius:13px;padding:14px 16px;border:1px solid #e9ede9;box-shadow:0 1px 2px rgba(21,32,26,.035)}
.sritem .it{font-size:14.5px;font-weight:700;color:#15201a}
.sritem .id2{font-size:12.5px;color:#7d847f;line-height:1.5;padding-top:4px}
.seeall{cursor:pointer;grid-column:1/-1;background:#fff;border:1px solid #e6e9e7;
  border-radius:14px;padding:14px;text-align:center;font-size:13.5px;
  font-weight:700;color:#15201a;font-family:inherit}

/* ---- panel cards & grids ---- */
.card{background:#fff;border-radius:18px;padding:22px 24px;border:1px solid #e6ebe7;box-shadow:0 1px 2px rgba(21,32,26,.04),0 6px 18px rgba(21,32,26,.06)}
/* align-items:stretch makes every card in a row take the tallest card's height,
   so Revenue / Valuation / Key Ratios line up evenly instead of ragged. */
.grid-auto{display:grid;grid-template-columns:repeat(auto-fit,
  minmax(min(280px,100%),1fr));gap:14px;align-items:stretch}
.grid-300{display:grid;grid-template-columns:repeat(auto-fit,
  minmax(min(300px,100%),1fr));gap:14px;align-items:stretch}
.grid-440{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;
  align-items:start}
.ct{font-size:17px;font-weight:700;color:#15201a;white-space:nowrap}
.ct-row{display:flex;align-items:baseline;justify-content:space-between}
.csub{font-size:12.5px;color:#9aa09d;padding:4px 0 8px}

/* clickable ⓘ info icon next to a title + the wide explanation popover */
.info{display:inline-flex;align-items:center;justify-content:center;width:16px;
  height:16px;border-radius:50%;border:1.4px solid #b6c0ba;color:#8b918e;
  font-size:10px;font-weight:700;font-style:italic;font-family:Georgia,'Times New Roman',serif;
  cursor:pointer;margin-left:7px;vertical-align:middle;flex:none;user-select:none;
  transition:background .15s,border-color .15s,color .15s}
.info:hover{border-color:#177245;color:#177245;background:#eef4f0}
#fctip.wide{white-space:normal;max-width:250px;line-height:1.55;font-size:12px;
  color:#e8efe9;padding:11px 13px}

/* year-on-year growth tiles (dashboard) */
.ftiles{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding-top:8px}
.ftile{background:#fbfcfb;border:1px solid #eef1ee;border-radius:14px;padding:14px 15px}
.ftile .fg{display:flex;align-items:center;gap:7px;font-size:22px;font-weight:800;
  letter-spacing:-.6px;color:#15201a}
.ftile .farr{font-size:13px}
.ftile .flbl{font-size:12.5px;color:#8b918e;padding-top:2px}
.ftile .flbl2{font-size:12px;color:#8b918e}
.ftile .fbig{font-size:22px;font-weight:800;letter-spacing:-.6px;color:#15201a;padding-top:4px}
.ftile .fnote{font-size:11.5px;color:#d9a441;font-weight:600;padding-top:10px;
  margin-top:10px;border-top:1px solid #eef1ee}
.ftile .frow{display:flex;align-items:center;gap:12px;margin-top:11px;padding-top:11px;
  border-top:1px solid #eef1ee}
.ftile .frow>span{display:flex;flex-direction:column;font-size:14px;font-weight:700;
  color:#15201a;font-family:ui-monospace,Menlo,monospace}
.ftile .frow small{font-size:10px;color:#9aa09d;font-weight:600;padding-top:2px;
  font-family:ui-monospace,Menlo,monospace}
.ftile .frow>i{width:1px;height:26px;background:#e6e9e6;flex:none}

/* Funda Score explainer (top of ratio deep dive) */
.scoreexp{margin-bottom:14px}
.scoreflex{display:flex;align-items:center;gap:22px;padding:10px 0 4px;flex-wrap:wrap}
.scorebadge{display:flex;flex-direction:column;align-items:center;gap:6px;flex:none;
  background:radial-gradient(130% 130% at 85% 15%,#2a9c62,#0f4a2c);
  border-radius:16px;padding:16px 22px;box-shadow:0 8px 22px rgba(15,74,44,.20)}
.scorebadge .sbig{font-size:34px;font-weight:800;letter-spacing:-1px;color:#fff;line-height:1}
.scorebadge .sbig small{font-size:15px;color:rgba(255,255,255,.8);font-weight:700}
.scorebadge .sverdict{font-size:10.5px;font-weight:800;letter-spacing:1px;color:#eafff3;
  background:rgba(255,255,255,.2);border-radius:20px;padding:3px 12px}
.scorenar{flex:1;min-width:260px;font-size:13.5px;line-height:1.65;color:#3f4744}
.scorenar b{color:#15201a;font-weight:700}
.pillars{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(230px,100%),1fr));
  gap:10px 26px;padding-top:14px;margin-top:6px;border-top:1px solid #eef1ee}
.prow{display:flex;align-items:center;gap:10px}
.prow .pl{font-size:12.5px;color:#5f6663;min-width:96px;font-weight:600}
.prow .ptrack{flex:1;height:7px;background:#eef1ee;border-radius:6px;overflow:hidden}
.prow .pfill{height:100%;border-radius:6px}
.prow b{font-size:13px;font-family:ui-monospace,Menlo,monospace;min-width:22px;text-align:right}
.pilltag{font-size:12px;font-weight:700;color:#177245;border:1.5px solid #cfe2d7;
  border-radius:20px;padding:6px 12px}

/* revenue trend pill bars (reference design) */
.pill-head{display:flex;align-items:baseline;justify-content:space-between}
.pill-note{font-size:12.5px;color:#9aa09d}
.pill-row{display:flex;align-items:flex-end;gap:10px;height:176px;
  padding:52px 2px 0;min-width:0}
.pill-col{flex:1;min-width:0;display:flex;flex-direction:column;
  align-items:center;gap:9px}
.pill-col span{font-size:13px;color:#8b918e}
.pill-wrap{position:relative;width:100%;display:flex;align-items:flex-end;
  justify-content:center}
.pill{width:100%;border-radius:40px;transition:filter .2s ease}
.pill-col:hover .pill{filter:brightness(1.06)}
.pill-tag{position:absolute;top:-32px;left:50%;transform:translateX(-50%);
  background:#eef4f0;border-radius:8px;padding:4px 8px;font-size:11.5px;
  font-weight:700;color:#0f5b34;white-space:nowrap}

/* stat mini-cards (ratio deep dive) */
.statgrid{display:grid;grid-template-columns:repeat(auto-fit,
  minmax(min(180px,100%),1fr));gap:14px}

/* ratio page header */
.pghead{display:flex;align-items:baseline;gap:14px;padding:2px 4px 0;flex-wrap:wrap}
.pghead .pt{font-size:26px;font-weight:800;letter-spacing:-.7px;color:#15201a}
.pghead .ps{font-size:13.5px;color:#8b918e}
.leftstack{flex:1 1 240px;min-width:0;display:flex;flex-direction:column;gap:14px}
.rightstack{flex:2 1 380px;min-width:0;display:flex;flex-direction:column;gap:14px}
.rowwrap{display:flex;gap:14px;flex-wrap:wrap;align-items:flex-start}
.strip{background:#fff;border-radius:18px;padding:16px 20px;border:1px solid #e6ebe7;box-shadow:0 1px 2px rgba(21,32,26,.04),0 6px 18px rgba(21,32,26,.06)}
.strip .slabel{font-family:ui-monospace,Menlo,monospace;font-size:10px;
  font-weight:700;letter-spacing:1.4px;color:#8b918e;padding-bottom:8px}
.dialcard-hd{text-align:center}
.dialcard-hd .t{font-size:16px;font-weight:700;color:#15201a}
.dialcard-hd .s{font-size:12.5px;color:#9aa09d;padding-top:3px}

/* sector lens */
.why{background:#fff;border-radius:18px;padding:20px 24px;
  border-left:4px solid #7f7de0}
.why .wl{font-family:ui-monospace,Menlo,monospace;font-size:10.5px;font-weight:700;
  letter-spacing:1.4px;color:#6f6dd0;padding-bottom:10px}
.why p{font-size:14.5px;line-height:1.65;color:#5f6663}
.healthrow{display:flex;align-items:center;gap:18px;flex-wrap:wrap}
.healthrow .htxt{flex:1;min-width:120px}
.healthrow .htxt .t{font-size:16px;font-weight:700;color:#15201a}
.healthrow .htxt .s{font-size:12.5px;color:#9aa09d;padding-top:4px}
.legendcol{display:flex;flex-direction:column;gap:9px;flex:1 1 240px;min-width:200px}
.legrow{display:flex;align-items:center;gap:8px}
.legrow i{width:11px;height:11px;border-radius:50%;flex:none}
.legrow span{font-size:12.5px;color:#5f6663}

/* statements */
.stmttabs{display:flex;align-items:center;gap:6px;padding-bottom:16px;flex-wrap:wrap}
.stmttab{cursor:pointer;padding:9px 16px;border-radius:11px;font-size:13px;
  font-weight:700;background:#f4f5f3;color:#5f6663;font-family:inherit;border:none}
.stmttab.on{background:#177245;color:#fff}
.pcttoggle{cursor:pointer;display:flex;align-items:center;gap:9px;user-select:none}
.pctbox{width:17px;height:17px;border-radius:5px;background:#177245;border:2px solid
  #177245;display:flex;align-items:center;justify-content:center;color:#fff;
  font-size:11px;font-weight:800}
.pctbox.off{background:#fff;border-color:#c9cec9;color:transparent}
.pctlbl{font-size:13px;color:#3f4744}
.stmtfoot{display:flex;align-items:center;gap:12px;padding-top:14px;flex-wrap:wrap}
.stmtfoot .note{margin-left:auto;font-size:12px;color:#9aa09d}
table.stmt{width:100%;border-collapse:collapse}
table.stmt th{padding:10px 12px;font-size:11.5px;letter-spacing:.8px;color:#8b918e;
  font-weight:700;text-align:right;border-bottom:1px solid #eceeec;
  font-family:ui-monospace,Menlo,monospace}
table.stmt th:first-child{text-align:left;position:sticky;left:0;background:#fafbfa;
  min-width:200px;font-size:14px;letter-spacing:0;color:#15201a}
table.stmt td{padding:10px 14px;text-align:right;border-bottom:1px solid #f1f3f1;
  white-space:nowrap;cursor:default}
table.stmt td:first-child{text-align:left;position:sticky;left:0;white-space:nowrap;
  font-size:15px;font-weight:500;color:#3f4744}
table.stmt tr.head td{font-weight:700;color:#15201a;background:#f5f9f7}
.val{font-size:17px;font-weight:600;color:#15201a;letter-spacing:-.2px;
  font-family:ui-monospace,Menlo,monospace}
tr.head .val{font-weight:800}
.pctsub{font-size:13px;padding-top:3px;font-weight:600;
  font-family:ui-monospace,Menlo,monospace}
.tblwrap{overflow:auto;border:1px solid #eceeec;border-radius:12px}

/* peers */
.peerlist{display:flex;flex-direction:column;gap:13px}
.peerrow{display:flex;align-items:center;gap:13px}
.peerav{width:34px;height:34px;border-radius:11px;flex:none}
.peermain{flex:1;min-width:0}
.peername{font-size:14.5px;font-weight:600;color:#15201a}
.peersub{font-size:12px;color:#9aa09d}
.peersub b{color:#15201a;font-weight:600}
.peertag{font-size:11.5px;font-weight:700;border-radius:8px;padding:5px 10px;
  white-space:nowrap;flex:none}
.ptag.g{color:#177245;background:#eef4f0}
.ptag.w{color:#8a7a2e;background:#f8f4e3}
.ptag.r{color:#a4483f;background:#faeeec}
.addpeer{display:flex;gap:8px;flex-wrap:wrap;padding-top:12px;
  border-top:1px dashed #eceeec;margin-top:12px}
.addpeer input{border:1px solid #e4e7e5;border-radius:9px;padding:7px 10px;
  font-family:inherit;font-size:12.5px;width:110px}
.addpeer button{background:#f4f5f3;border:1px solid #e4e7e5;border-radius:9px;
  padding:7px 12px;font-family:inherit;font-weight:700;font-size:12.5px;
  color:#15201a;cursor:pointer}

/* toast + tooltip */
#toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);
  background:#0f2a1e;color:#fff;font-size:12.5px;padding:9px 16px;
  border-radius:10px;opacity:0;pointer-events:none;transition:opacity .25s;z-index:99}
#fctip{position:absolute;display:none;pointer-events:none;background:#0f2a1e;
  opacity:.97;border-radius:8px;padding:8px 10px;z-index:60;min-width:96px;
  font-size:10.5px;line-height:15px;color:#cfe0d7;white-space:nowrap;
  box-shadow:0 6px 18px rgba(0,0,0,.25)}
#fctip b{color:#fff;font-weight:700}
#fctip .yr{font-size:11px;font-weight:700;margin-bottom:4px}
#fctip span{display:flex;align-items:center;gap:6px}
#fctip i{width:7px;height:7px;border-radius:50%;flex:none}
#fctip .v{margin-left:auto;padding-left:12px;color:#fff;font-weight:700;
  font-family:ui-monospace,Menlo,monospace}
.hit{cursor:crosshair}

/* --- refined "soft card" polish (imitates the provided design file) --- */
.kpi.score{border:none;box-shadow:0 8px 22px rgba(15,74,44,.20)}
.herostat{box-shadow:0 1px 2px rgba(21,32,26,.05),0 4px 12px rgba(21,32,26,.05)}
main [style*="background:#fff"][style*="border-radius:18px"],
main [style*="background:#fff"][style*="border-radius:20px"]{
  box-shadow:0 1px 2px rgba(21,32,26,.04),0 6px 18px rgba(21,32,26,.06)}
"""

FC_DEFS = """
// Resolve #fctip lazily: this script runs before the #fctip element exists in the
// document, so capturing it at load time would leave `tip` null and every
// fcShow() would throw, silently killing all chart/diagram hover tooltips.
function _fctip(){var t=document.getElementById('fctip');
  if(!t){t=document.createElement('div');t.id='fctip';document.body.appendChild(t);}
  return t;}
// x,y are viewport (client) coords; #fctip is position:absolute, so add the
// scroll offset to get page coords. This keeps the tooltip glued to the cursor
// even when the frame is auto-fit to full height and the parent page scrolls
// (where position:fixed would place it off-screen).
function fcShow(x,y,html){var tip=_fctip();tip.className='';tip.innerHTML=html;tip.style.display='block';
  var sx=window.scrollX||0, sy=window.scrollY||0;
  let px=x+14;if(px+tip.offsetWidth>window.innerWidth-8)px=x-tip.offsetWidth-14;
  let py=Math.max(4,Math.min(y-tip.offsetHeight/2,window.innerHeight-tip.offsetHeight-4));
  tip.style.left=(Math.max(4,px)+sx)+'px';
  tip.style.top=(py+sy)+'px';}
function fcHide(){var tip=document.getElementById('fctip');if(tip)tip.style.display='none';}
// click a ⓘ icon -> show its plain-language explanation; click anywhere hides it.
function fcInfo(x,y,html){var tip=_fctip();tip.className='wide';tip.innerHTML=html;
  tip.style.display='block';var sx=window.scrollX||0,sy=window.scrollY||0;
  let px=x+12;if(px+tip.offsetWidth>window.innerWidth-8)px=x-tip.offsetWidth-12;
  let py=Math.min(y+16,window.innerHeight-tip.offsetHeight-4);
  tip.style.left=(Math.max(4,px)+sx)+'px';tip.style.top=(Math.max(4,py)+sy)+'px';}
function fcBindInfo(){
  document.querySelectorAll('.info').forEach(el=>{
    el.addEventListener('click',function(e){e.stopPropagation();
      fcInfo(e.clientX,e.clientY,el.getAttribute('data-info'));});});
  document.addEventListener('click',function(){fcHide();});}
// Jump to another page by clicking its sidebar nav button in the parent app.
function fcGoto(pg){try{var d=window.parent.document;
  var b=d.querySelector('[class*="st-key-nav-'+pg+'"] button');
  if(b){b.click();}}catch(e){}}
function fcBindTips(){
  document.querySelectorAll('[data-tt]').forEach(el=>{
    el.addEventListener('mousemove',e=>{
      fcShow(e.clientX,e.clientY,el.getAttribute('data-tt'));});
    el.addEventListener('mouseleave',fcHide);});}
function fcColumns(cid,Y,L,fmt){
  const svg=document.getElementById(cid);
  if(!svg)return;
  const cols=svg.querySelectorAll('.hit'), xl=svg.querySelectorAll('.xline');
  cols.forEach((el,i)=>{
    el.addEventListener('mousemove',e=>{
      const rows=L.map(l=>'<span><i style="background:'+l[1]+'"></i>'+l[0]+
        '<span class="v">'+fmt(l[2][i])+'</span></span>').join('');
      fcShow(e.clientX,e.clientY,'<div class="yr">'+Y[i]+'</div>'+rows);
      xl.forEach(x=>x.style.display='none');if(xl[i])xl[i].style.display='block';});
    el.addEventListener('mouseleave',()=>{fcHide();
      xl.forEach(x=>x.style.display='none');});});}
function toast(msg){const t=document.getElementById('toast');
  t.textContent=msg;t.style.opacity=1;
  clearTimeout(t._h);t._h=setTimeout(()=>t.style.opacity=0,1800);}
"""

FC_BIND = """
fcBindTips();
fcBindInfo();
"""

SHELL_JS = """
// statements tabs
document.querySelectorAll('.stmttab').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('.stmttab').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');
  document.querySelectorAll('.stmttable').forEach(t=>
    t.style.display=t.id==='tbl-'+b.dataset.tab?'block':'none');}));
// % change toggle
const pt=document.getElementById('pcttoggle');
const pb=document.getElementById('pctbox');
if(pt&&pb)pt.addEventListener('click',()=>{
  pb.classList.toggle('off');
  const on=!pb.classList.contains('off');
  document.querySelectorAll('.pctsub').forEach(e=>
    e.style.display=on?'block':'none');});
// search filters statement rows + peer names
const sq=document.getElementById('fcsearch');
if(sq)sq.addEventListener('input',()=>{
  const q=sq.value.trim().toLowerCase();
  document.querySelectorAll('.stmt tr[data-name]').forEach(tr=>{
    tr.style.display=!q||tr.dataset.name.includes(q)?'':'none';});});
// see all toggle
const sa=document.getElementById('seeall');
if(sa)sa.addEventListener('click',()=>{
  const open=sa.dataset.open!=='1';
  sa.dataset.open=open?'1':'0';
  sa.textContent=open?'Show fewer':'See all';
  document.querySelectorAll('.moreitem').forEach(e=>
    e.style.display=open?'':'none');
  const n=open?sa.dataset.total:'3';
  document.querySelectorAll('.cnt').forEach(c=>c.textContent=n);});
// peer add
const pa=document.getElementById('peeraddbtn');
if(pa)pa.addEventListener('click',()=>{
  const nm=document.getElementById('p-name').value.trim();
  if(!nm){toast('Enter a company name');return;}
  const pe=parseFloat(document.getElementById('p-pe').value);
  const roe=parseFloat(document.getElementById('p-roe').value);
  const de=parseFloat(document.getElementById('p-de').value);
  const av=['#e8f1ec','#f2f0e6','#f0eaf2','#f6ebe6'][
    document.querySelectorAll('.peerrow').length%4];
  let sub='ROE '+(isFinite(roe)?'<b>'+roe.toFixed(1)+'%</b>':'n/a');
  if(isFinite(de))sub+=' · D/E '+de.toFixed(2);
  let tag;
  if(!(pe>0))tag='<span class="peertag r">Loss</span>';
  else{const cls=pe>=45?'r':pe>=32?'w':'g';
    tag='<span class="peertag '+cls+'">P/E '+pe.toFixed(1)+'</span>';}
  const div=document.createElement('div');
  div.className='peerrow';
  div.setAttribute('data-name',nm.toLowerCase());
  div.innerHTML='<div class="peerav" style="background:'+av+'"></div>'+
    '<div class="peermain"><div class="peername"></div>'+
    '<div class="peersub">'+sub+'</div></div>'+tag;
  div.querySelector('.peername').textContent=nm;
  const list=document.querySelector('.peerlist');
  list.appendChild(div);
  ['p-name','p-pe','p-roe','p-de'].forEach(id=>
    document.getElementById(id).value='');
  toast(nm+' added to comparison');});
"""


# Self-size the component frame to its actual content. components.html is given a
# fixed height by Streamlit, which leaves a band of empty shell below short pages
# (and an inner scrollbar on tall ones). The srcdoc iframe is same-origin, so from
# inside we can set the frame's own height and collapse the Streamlit container to
# match — giving every page an exact fit at any width, with no trailing empty space.
FIT_JS = """
(function(){
  function fit(){
    try{
      var s=document.getElementById('shell'); if(!s) return;
      /* #shell sits inside body's 24px top + 34px bottom padding, so the frame
         needs the shell height plus that 58px to fit exactly with no trailing gap. */
      var h=Math.ceil(s.getBoundingClientRect().height)+58;
      /* Official Streamlit resize channel — works even when the component iframe
         is served cross-origin (where window.frameElement below is blocked). */
      try{ window.parent.postMessage(
        {isStreamlitMessage:true, type:'streamlit:setFrameHeight', height:h}, '*'); }catch(e){}
      /* Same-origin fallback: size the frame and collapse every Streamlit wrapper
         above it so the page ends exactly at the content. Only write on a real
         change so re-fitting can't churn. */
      try{ var fe=window.frameElement;
        if(fe){ var cur=parseInt(fe.style.height)||0;
          if(Math.abs(cur-h)>1){
            fe.style.setProperty('height', h+'px', 'important');
            fe.setAttribute('height', h); }
          var el=fe.parentElement;
          for(var i=0;i<5 && el;i++){
            var tid=el.getAttribute && el.getAttribute('data-testid');
            if(tid==='stElementContainer'||tid==='stVerticalBlock'||tid==='stVerticalBlockBorderWrapper'){
              if(el.style.height!=='auto') el.style.height='auto';
              el.style.minHeight='0px'; }
            if(tid==='stMain'||tid==='stAppViewContainer') break;
            el=el.parentElement; } }
      }catch(e){}
    }catch(e){}
  }
  function schedule(){fit();for(var k=1;k<=12;k++)setTimeout(fit,k*250);}
  window.addEventListener('load',schedule); schedule();
  if(window.ResizeObserver){
    var ro=new ResizeObserver(fit);
    var s=document.getElementById('shell'); if(s) ro.observe(s);
    /* Zooming out reflows the cards shorter but does not resize #shell (it is
       max-width capped); observing the root catches the zoom/width change so the
       frame re-fits to the new, shorter content instead of scrolling past it. */
    ro.observe(document.documentElement);
    if(document.body) ro.observe(document.body);
  }
  window.addEventListener('resize',fit);
  /* The VisualViewport API fires on browser zoom, which plain 'resize' and the
     max-width-capped #shell observer miss — this is what makes the frame re-fit
     (shrink) when the user zooms out instead of leaving empty space. */
  if(window.visualViewport){ window.visualViewport.addEventListener('resize',fit);
    window.visualViewport.addEventListener('scroll',fit); }
  /* Low-frequency safety net for anything the events miss (kept lightweight). */
  setInterval(fit, 1000);
})();
"""


def _esc(s) -> str:
    return escape(str(s))


# Plain-language, one-line explanation for each chart/diagram, with whether a
# higher or lower reading is better. Shown when the reader clicks the ⓘ next to a
# title. Written for someone with no finance background.
CHART_INFO = {
    "Revenue Trend": "Total sales the company made each year — its top line. (higher is better)",
    "Valuation": "What the market is paying for the company versus what it earns and owns. (cheaper for the same earnings is better)",
    "Key Ratios": "A quick snapshot of the most important health checks in one place. (mostly higher is better)",
    "Year-on-year growth": "How much sales, costs and profit changed versus last year. (rising sales and profit are good; slower cost growth is good)",
    "Financial Health": "An overall score blending profit, safety and cash strength into one number. (higher is better)",
    "Where each ₹100 of sales goes": "For every ₹100 of sales, how much is spent on each cost and how much is kept as profit. (keeping more is better)",
    "Margin ladder": "The share of each sale left as profit after each layer of cost. (higher is better)",
    "Returns": "How much profit the company makes on the money invested in it. (higher is better)",
    "Leverage & solvency": "How much debt the company carries and how easily its profit covers the interest. (less debt and higher cover are better)",
    "Working capital cycle": "How many days cash is stuck in stock and unpaid customer bills before it returns. (fewer days is better)",
    "Cash flow mix": "Where cash comes from and goes — running the business, investing, and financing. (strong cash from operations is best)",
    "Turnover & efficiency": "How many times a year the company turns its assets into sales. (higher is better)",
    "Total assets, by component": "What the company owns, split into types, over time.",
    "Total liabilities & equity": "How the company is funded — borrowed money versus owners' money.",
    "Return on capital employed": "How efficiently the company turns its total capital into profit. (higher is better)",
    "Applied benchmarks": "The sector-specific pass marks each ratio is judged against.",
    "How the ratios move together": "Which ratios tend to rise and fall together across history.",
    "Financial health": "An overall score blending profit, safety and cash strength. (higher is better)",
}


def _ct(name: str, extra: str = "") -> str:
    """A card title with a clickable ⓘ that explains it in plain language."""
    info = CHART_INFO.get(name)
    icon = (f'<span class="info" data-info="{_esc(info)}">i</span>') if info else ""
    return f'<span class="ct"{(" " + extra) if extra else ""}>{name}</span>{icon}'


def _doc(body: str, scripts: str = "", extra_css: str = "") -> str:
    """Full standalone doc.

    Order matters: the tooltip *functions* are defined before the body so the
    chart scripts (which sit inline next to their SVG and call fcColumns
    immediately) can bind; the [data-tt] binding runs after the body exists.
    """
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:'
        'wght@400;500;600;700;800&display=swap" rel="stylesheet">'
        f"<style>{SHELL_CSS}{extra_css}</style></head>"
        "<body>"
        f"<script>{FC_DEFS}</script>"
        f'<div id="shell"><main>{body}</main></div>'
        f'<div id="toast"></div><div id="fctip"></div>'
        f"<script>{FC_BIND}</script>"
        f"<script>{SHELL_JS}</script>{scripts}"
        f"<script>{FIT_JS}</script>"
        "</body></html>"
    )


def _kpi_cards(model, result) -> list[str]:
    def pct(*names):
        s = S.pct_series(S.ser(model, *names))
        return float(s.iloc[-1]) if not s.empty else None

    circ = '<div class="circ">&#8599;</div>'
    pe = S.ser(model, "PE Ratio")
    pe = pe[(pe > 0) & (pe < 1000)]
    pe_big, pe_ft = "n/a", "not in this workbook"
    if not pe.empty:
        latest, med = float(pe.iloc[-1]), float(pe.median())
        cheaper = latest < med
        pe_big = f"{latest:.1f}"
        pe_ft = (f'<span class="chip g">\u25bc</span>10-yr median {med:.1f}' if cheaper
                 else f'<span class="chip w">\u25b2</span>10-yr median {med:.1f}')
    roe = pct("Return on Equity (ROE) %")
    roe_s = S.ser(model, "Return on Equity (ROE) %")
    roe_big, roe_ft = "n/a", "not in this workbook"
    if not roe_s.empty:
        latest = float(S.pct_series(roe_s).iloc[-1])
        prev = float(roe_s.iloc[-2]) * (100 if abs(float(roe_s.iloc[-2])) <= 3 else 1) \
            if len(roe_s) > 1 else None
        prev_txt = f"{prev:.1f}%" if prev is not None else ""
        delta = latest - prev if prev is not None else 0
        chip = (f'<span class="chip g">{delta:+.1f} \u25b2</span>'
                if delta >= 0 else f'<span class="chip r">{delta:+.1f} \u25bc</span>')
        roe_big = f"{latest:.1f}%"
        roe_ft = f"{chip}{prev_txt} was previous year" if prev is not None \
            else "single-year history"
    de_s = S.ser(model, "Debt to Equity Ratio")
    de_big = f"{float(de_s.iloc[-1]):.2f}" if not de_s.empty else "n/a"
    scored = result.metric("Debt to Equity Ratio")
    if scored is None:
        de_ft = "no sector benchmark for this workbook"
    elif scored.score >= 66:
        de_ft = "Comfortably within sector norms"
    elif scored.score >= 40:
        de_ft = "Within sector norms, cover is thin"
    else:
        de_ft = "Above the sector comfort zone"

    # The score card's arrow jumps to Ratio deep dive, where the score breakdown
    # is explained. cursor:pointer + title tell the reader it is clickable.
    score_circ = ('<div class="circ scorelink" onclick="fcGoto(\'ratios\')" '
                  'title="See how this score is built" '
                  'style="cursor:pointer">&#8599;</div>')
    score_card = (
        f'<div class="kpi score"><div class="hd"><span class="name">Funda Score</span>'
        f"{score_circ}</div>"
        f'<div class="big">{result.total_score:.0f}<small>/100</small></div>'
        f'<div class="ft"><span class="chip">{result.verdict}</span>sector adjusted'
        f"</div></div>")
    mk = lambda name, big, ft: (
        f'<div class="kpi"><div class="hd"><span class="name">{name}</span>{circ}</div>'
        f'<div class="big">{big}</div><div class="ft">{ft}</div></div>')
    return [score_card, mk("P/E Ratio", pe_big, pe_ft),
            mk("Return on Equity", roe_big, roe_ft),
            mk("Debt / Equity", de_big, de_ft)]


def _score_rationale(result) -> str:
    """Top-of-ratios explainer: what the Funda Score is and what drove it."""
    pillars = result.pillar_scores or {}
    bars = "".join(
        f'<div class="prow"><span class="pl">{_esc(p)}</span>'
        f'<div class="ptrack"><div class="pfill" style="width:{max(3, s):.0f}%;'
        f'background:{viz.band(s)}"></div></div>'
        f'<b style="color:{viz.band(s)}">{s:.0f}</b></div>'
        for p, s in sorted(pillars.items(), key=lambda kv: -kv[1]))
    ranked = sorted(result.metrics, key=lambda m: m.score, reverse=True)
    ups = ", ".join(S.short_name(m.metric) for m in ranked[:2]) or "—"
    downs = ", ".join(S.short_name(m.metric) for m in reversed(ranked[-2:])) or "—"
    info = ("A 0–100 health rating, weighted for this sector. It blends the "
            "area sub-scores below into one number. (higher is better)")
    narrative = (
        f"The Funda Score is a 0–100 health rating, tuned to the "
        f"<b>{_esc(result.sector.name)}</b> sector. {_esc(result.company.title())} "
        f"scores <b>{result.total_score:.0f}/100</b>, which lands in the "
        f"<b>{result.verdict.lower()}</b> band. It is lifted most by "
        f"<b>{_esc(ups)}</b> and held back by <b>{_esc(downs)}</b>. Each bar is one "
        "area's 0–100 sub-score; the total is their weighted average.")
    return (
        '<div class="card scoreexp"><div class="ct-row">'
        f'<span class="ct">How the Funda Score is built</span>'
        f'<span class="info" data-info="{_esc(info)}">i</span></div>'
        '<div class="scoreflex">'
        f'<div class="scorebadge"><div class="sbig">{result.total_score:.0f}'
        '<small>/100</small></div>'
        f'<span class="sverdict">{result.verdict}</span></div>'
        f'<div class="scorenar">{narrative}</div></div>'
        f'<div class="pillars">{bars}</div></div>')


def _drivers_html(result) -> str:
    ranked = sorted(result.metrics, key=lambda m: m.score, reverse=True)[:6]
    rows = "".join(
        f'<div class="drow"><div class="dl"><span>{_esc(S.short_name(m.metric))}'
        f'</span><b style="color:{viz.band(m.score)}">{round(m.score)}</b></div>'
        f'<div class="track"><div class="fill" '
        f'style="width:{m.score:.0f}%;background:{viz.band(m.score)}"></div></div></div>'
        for m in ranked)
    return f'<div class="drivers">{rows}</div>'


def _split_note(text: str) -> tuple[str, str]:
    for sep in (" \u2014 ", " \u2013 ", ": "):
        if sep in text:
            head, _, tail = text.partition(sep)
            return head.strip(), tail.strip()
    cut = text.find(". ")
    if 30 < cut < 110:
        return text[:cut].strip(), text[cut + 1:].strip()
    return text.strip(), ""


def _strengths_risks(note: dict, result) -> str:
    strengths = list(note.get("strengths") or result.strengths)
    risks = list(note.get("risks") or result.concerns)

    def panel(title, items, kind, colour):
        shown = items[:3]
        extras = items[3:]
        blocks = "".join(
            f'<div class="sritem"><div class="it">{_esc(t)}</div>'
            f'<div class="id2">{_esc(d)}</div></div>'
            for t, d in (_split_note(str(i)) for i in shown))
        blocks += "".join(
            f'<div class="sritem moreitem" style="display:none">'
            f'<div class="it">{_esc(t)}</div><div class="id2">{_esc(d)}</div></div>'
            for t, d in (_split_note(str(i)) for i in extras))
        return (f'<div class="srpanel {kind}"><div class="srhead">'
                f'<span class="t">{title}</span>'
                f'<span class="cnt" style="background:{colour}">'
                f'{len(items[:3]) if items else 0}</span></div>'
                f'<div class="srlist">{blocks}</div></div>')

    total = max(len(strengths), len(risks), 3)
    see_all = ""
    if len(strengths) > 3 or len(risks) > 3:
        see_all = ('<div class="seeall" id="seeall" data-open="0" '
                   f'data-total="{total}">See all</div>')
    return ('<div class="srgrid">' +
            panel("Ratio Strengths", strengths, "str", "#177245") +
            panel("Ratio Risks", risks, "rsk", "#a4483f") + see_all + "</div>")


def _valuation(model) -> str:
    segs = []
    mcap = model.meta.get("market_cap")
    sales_v = S.last_two(S.ser(model, "Sales"))[0]
    net_v = S.last_two(S.ser(model, "Net Profit"))[0]
    if mcap and sales_v and net_v:
        segs = [("Market Cap", mcap, "#9aa09d"), ("Revenue", sales_v, "#177245"),
                ("Net income", net_v, "#5fd0a0")]
    # The P/E and P/S multiples rows were removed at the user's request; the
    # valuation card now shows the composition donut alone.
    if not segs:
        return '<p class="csub">Valuation inputs not present in this workbook.</p>'
    donut, _ = viz.donut(segs, S.cr(mcap), "crore mcap")
    return donut


def _key_ratios(model) -> str:
    return _kr_fallback(model)


def _kr_fallback(model) -> str:
    rows = [("Gross Margin", "Profitability", ("Gross Margin",), True),
            ("ROCE", "Efficiency", ("Return on Capital Employed (ROCE) %",), True),
            ("Interest Coverage", "Solvency", ("Interest Coverage Ratio",), False),
            ("EBITDA Margin", "Operating", ("EBITDA Margin",), True),
            ("Cash Cycle", "Working capital", ("Cash Conversion Cycle",), False)]
    out = []
    for label, fam, names, is_pct in rows:
        s = S.ser(model, *names)
        if s.empty:
            continue
        v = float(S.pct_series(s).iloc[-1]) if is_pct else float(s.iloc[-1])
        txt = f"{v:.1f}%" if is_pct else f"{v:.1f}x" if not is_pct and "Coverage" in label \
            else f"{v:.0f} d"
        out.append(f'<div style="display:flex;align-items:center;'
                   f'justify-content:space-between;gap:10px"><div style="min-width:0">'
                   f'<div style="font-size:14px;font-weight:600;color:{INK}">{label}</div>'
                   f'<div style="font-size:11.5px;color:{FAINT}">{fam}</div></div>'
                   f'<span style="font-size:15px;font-weight:700;color:{INK};'
                   f'flex:none;font-family:{MONO}">{txt}</span></div>')
    return (f'<div style="display:flex;flex-direction:column;gap:11px">'
            + "".join(out) + "</div>")


def _peers(peers: list[dict]) -> str:
    av = ["#e8f1ec", "#f2f0e6", "#f0eaf2", "#f6ebe6"]
    rows = []
    for i, p in enumerate(peers or []):
        pe, roe, de = p.get("pe"), p.get("roe"), p.get("de")
        sub = f'ROE <b>{roe:.1f}%</b>' if roe is not None else "ROE n/a"
        sub += f" · D/E {de:.2f}" if de is not None else ""
        if pe is not None and pe > 0:
            cls = "r" if pe >= 45 else "w" if pe >= 32 else "g"
            tag = f'<span class="peertag {cls}">P/E {pe:.1f}</span>'
        else:
            tag = '<span class="peertag r">Loss</span>'
        rows.append(f'<div class="peerrow" data-name="{_esc(p["name"]).lower()}">'
                    f'<div class="peerav" style="background:{av[i % 4]}"></div>'
                    f'<div class="peermain"><div class="peername">'
                    f'{_esc(p["name"])}</div><div class="peersub">{sub}</div></div>'
                    f"{tag}</div>")
    empty = ("<p class='csub'>No peers yet - add companies to compare them "
             "side by side.</p>") if not rows else ""
    form = ('<div class="addpeer">'
            '<input id="p-name" placeholder="Company">'
            '<input id="p-pe" type="number" step="0.1" placeholder="P/E">'
            '<input id="p-roe" type="number" step="0.1" placeholder="ROE %">'
            '<input id="p-de" type="number" step="0.05" placeholder="D/E">'
            '<button id="peeraddbtn" type="button">Add peer</button></div>')
    return (f'<div class="peerlist">{"".join(rows)}</div>{empty}{form}')


def _statements_tables(model, query: str) -> str:
    tables = []
    for tab_key, label in (("is", "Income Statement"), ("ra", "Ratio Analysis"),
                           ("cs", "Common Size")):
        html = S.statements_html(model, label, True, query)
        disp = "block" if tab_key == "is" else "none"
        tables.append(f'<div class="stmttable" id="tbl-{tab_key}" '
                      f'style="display:{disp}">{html}</div>')
    return "".join(tables)


# ==========================================================================
# public builders - one per Streamlit page
# ==========================================================================
# Initial frame heights are deliberately modest floors, not generous ones: the
# in-frame fit tightens them to the exact content, and scrolling=True absorbs any
# underestimate on a narrow window. A generous floor is what leaves empty space
# when the fit lags, so keep these close to the real content height.
HEIGHTS = {"dashboard": 1600, "ratios": 1800, "sector": 1400, "statements": 900}


def _topbar(current: str) -> str:
    pills = "".join(
        f'<button class="stmttab {"on" if key == current else ""}" '
        f'data-goto="{key}" '
        f'onclick="if(this.dataset.goto!==\'{current}\')toast(\'Switch pages from '
        f'the left menu\')">{label}</button>'
        for key, label in (("dashboard", "Dashboard"),
                           ("ratios", "Ratio deep dive"),
                           ("sector", "Sector lens"),
                           ("statements", "Statements")))
    return (f'<div class="topbar"><div class="searchpill"><span class="lens"></span>'
            f'<input id="fcsearch" placeholder="Search company or ticker">'
            f'<span class="kbd">\u2318 K</span></div>'
            f'<div class="topright">{pills}'
            f'<div class="monitor"><i></i></div>'
            f'<button class="aibtn" onclick="toast(\'Use the left menu - Ask the '
            f'analyst\')"><span class="ic">\u2726</span><span><span class="t1">'
            f'Ask Analyst AI</span><br><span class="t2">SECTOR AWARE</span></span>'
            f"</button></div></div>")


def _hero(model, result) -> str:
    price = model.meta.get("current_price")
    mcap = model.meta.get("market_cap")
    price_html = ""
    if price:
        whole, _, frac = f"{price:,.2f}".partition(".")
        price_html = ('<div><div class="lbl">LAST TRADED PRICE</div>'
                      f'<div style="display:flex;align-items:baseline;gap:9px;'
                      f'padding-top:5px"><span class="val">\u20b9{whole}<small>.{frac}'
                      f"</small></span></div></div>")
    mcap_html = ""
    if mcap:
        pretty = (f"\u20b9{mcap / 1e5:.2f}L cr" if mcap >= 1e5
                  else f"\u20b9{mcap:,.0f} cr")
        rule = '<div class="vrule"></div>' if price else ""
        mcap_html = f'{rule}<div><div class="lbl">MKT CAP</div>'\
                    f'<div class="mcap">{pretty}</div></div>'
    stats = (f'<div class="herostat">{price_html}{mcap_html}</div>') \
        if (price or mcap) else ""

    years = S.full_years(model)
    sub = (f'<span class="ticker">{_esc(result.sector.name.upper())}</span>'
           f'<span class="dotsep">\u00b7</span>'
           f'<span class="ticker">{years[0]}\u2013{years[-1]}</span>'
           f'<span class="dotsep">\u00b7</span>'
           f'<span class="ticker">{len(years)} PERIODS</span>')

    # Export Report sits in the hero, exactly where the reference puts it.
    # The PDF is embedded as a data URI; Streamlit's component iframes carry
    # no sandbox, so the download goes straight to the browser.
    export = ""
    try:
        from .report import build_pdf
        pdf64 = base64.b64encode(build_pdf(model, result)).decode()
        export = (f'<a class="exportbtn" download="{_esc(model.company)}'
                  f'_fundacheck_report.pdf" '
                  f'href="data:application/pdf;base64,{pdf64}">Export Report</a>')
    except Exception:                                    # noqa: BLE001
        export = ('<span class="exportbtn" onclick="toast(\'Export unavailable '
                  'for this model\')" style="cursor:pointer">Export Report</span>')

    return (f'<div class="hero"><div><h1>{_esc(model.company.title())}</h1>'
            f'<div class="herosub">{sub}</div></div>'
            f'<div class="heroright">{stats}{export}</div></div>')


def dashboard_shell(model, result, note: dict, peers: list[dict]) -> tuple[str, int]:
    emoji = {"STRONG": "\U0001F603", "NEUTRAL": "\U0001F610"}.get(
        result.verdict, "\U0001F615")
    rail = result.colour if isinstance(result.colour, str) and \
        result.colour.startswith("#") else "#d9a441"

    summary = note.get("summary") or ""
    chips = (f'<div class="chips"><span class="vtag" style="color:'
             f'{result.colour};background:{result.colour}18;border:1px solid '
             f'{result.colour}55">{result.verdict}</span>'
             f'<span class="sect">SECTOR AWARE · '
             f'{_esc(result.sector.name.upper())}</span></div>')

    rev_html = D.revenue_trend(model)
    sankey_title, sankey_svg = D.income_sankey(model)
    cost_html, _cost_h = S.cost_card(model)

    body = "".join([
        _hero(model, result),
        f'<div class="kpigrid">{"".join(_kpi_cards(model, result))}</div>',
        (f'<div class="verdict" style="--rail:{rail}">'
         f'<div>{chips}<h2>{_esc(result.headline)} {emoji}</h2>'
         f"<p>{_esc(summary)}</p></div>{_drivers_html(result)}</div>"),
        _strengths_risks(note, result),
        '<div class="grid-auto">'
        '<div class="card"><div class="ct-row">' + _ct("Revenue Trend")
        + "</div>"
        f"{rev_html}</div>",
        f'<div class="card"><div class="ct-row">{_ct("Valuation")}'
        f'<span style="font-size:15px;color:#9aa09d">\u203a</span></div>'
        f'<div class="csub">Fundamental metrics to determine fair value</div>'
        f"{_valuation(model)}</div>",
        '<div class="card"><div class="ct-row">' + _ct("Key Ratios") + ""
        '<span class="pilltag">All</span></div>' + _key_ratios(model) + "</div>",
        "</div>",
        '<div class="grid-300">'
        '<div class="card"><div class="ct-row">'
        + _ct("Where each ₹100 of sales goes") + "</div>"
        '<div class="csub">Latest-year cost structure, ₹ per ₹100 of sales</div>'
        + cost_html + "</div>",
        '<div class="card" style="display:flex;flex-direction:column;align-items:'
        'center"><div style="align-self:flex-start">' + _ct("Financial Health")
        + "</div>" + viz.gauge(float(result.total_score),
                             f"{result.total_score:.0f}%")[0]
        + '<div style="display:flex;align-items:center;justify-content:center;gap:16px;'
          'flex-wrap:wrap;padding-top:12px">'
        + _gauge_inline_legend() + "</div></div></div>",
    ])
    if sankey_svg:
        sankey_info = ("Shows how each rupee of sales turns into profit after "
                       "paying every cost. (keeping more as profit is better)")
        body += (f'<div class="card"><div class="ct-row">'
                 f'<span class="ct">{_esc(sankey_title)}</span>'
                 f'<span class="info" data-info="{_esc(sankey_info)}">i</span></div>'
                 f'<div class="csub">Income statement flow, \u20b9 crore</div>'
                 f"{sankey_svg}</div>")
    # A mild UNDER-estimate (see ratios_shell): the parent-side fit grows the
    # frame to the exact content, and under-reserving avoids the trailing empty
    # band that an over-estimate leaves on the frame's reserved container.
    height = 1500 + (150 if sankey_svg else 0)
    return _doc(body, ""), height


def _gauge_inline_legend() -> str:
    risk_bg = "repeating-linear-gradient(-45deg,#d9dcd9 0 3px,#f2f3f1 3px 6px)"
    item = lambda c, t: (f'<div style="display:flex;align-items:center;gap:7px">'
                         f'<div style="width:11px;height:11px;border-radius:50%;'
                         f'background:{c}"></div>'
                         f'<span style="font-size:12.5px;color:#5f6663">{t}</span></div>')
    return item(viz.MID, "Strong") + item(GREEN_DARK, "Stable") + item(risk_bg, "Risk")


def ratios_shell(model, result) -> tuple[str, int]:
    flows_html = S.flows_card(model)
    rows = [(S.short_name(m.metric), m.display(m.latest), float(m.score))
            for m in result.metrics]
    sc_html, sc_h = viz.scorecard_chart(rows)
    charts = S.deepdive_charts(model)

    titles = {
        "margins": ("Margin ladder", "Gross \u2192 EBITDA \u2192 EBIT \u2192 Net"),
        "returns": ("Returns", "ROE \u00b7 ROCE \u00b7 ROA"),
        "leverage": ("Leverage & solvency", "Debt/equity bars \u00b7 interest cover line"),
        "wc": ("Working capital cycle", "Debtor + inventory \u2212 payable days"),
        "cash": ("Cash flow mix", "Operating \u00b7 investing \u00b7 financing, \u20b9 cr"),
        "turnover": ("Turnover & efficiency", "Times per year, latest vs 10-yr mean"),
        "assets": ("Total assets, by component", "Stacked, \u20b9 crore"),
        "liab": ("Total liabilities & equity", "Stacked, \u20b9 crore"),
    }
    chart_cards = "".join(
        f'<div class="card"><div class="ct-row">{_ct(t)}</div>'
        f'<div class="csub">{s2}</div>{html}</div>'
        for key, (t, s2) in titles.items() if key in charts
        for html, _h in [charts[key]])

    body = "".join([
        '<div class="pghead"><span class="pt">Ratio deep dive</span>'
        '<span class="ps">All nine categories from the Ratio Analysis sheet.</span>'
        "</div>",
        _score_rationale(result),
        '<div class="rowwrap">',
        f'<div style="flex:0 1 350px;min-width:280px">'
        f"{S.roce_card(model, result)}</div>",
        f'<div class="card" style="flex:1;min-width:300px">'
        f'<div class="ct-row">{_ct("Year-on-year growth")}</div>'
        f'<div class="csub">How sales, costs and profit moved, in \u20b9 crore</div>'
        f"{flows_html}</div>",
        "</div>",
        f'<div class="strip"><div class="slabel">RATIO SCORECARD \u2014 SCORED '
        f'AGAINST SECTOR BANDS</div>{sc_html}</div>',
        f'<div class="grid-440">{chart_cards}</div>',
    ])
    # Deliberately a mild UNDER-estimate. Streamlit reserves the declared height
    # on the frame's container; over-reserving leaves a tall empty band below the
    # content that the parent-side resizer cannot reclaim. Under-reserving has no
    # such cost — the resizer grows the frame to the real content and scrolling is
    # the safety net — so keep this just under a typical rendered page.
    est = 1100 + 120 * len(charts)
    return _doc(body, ""), min(2050, max(1400, est))


def sector_shell(model, result) -> tuple[str, int]:
    sectors, hot_name = S.sector_scores(model, result)
    bars_html, bars_h = viz.sector_bars(sectors, hot_name)
    gauge_html, _gh = viz.gauge(float(result.total_score),
                                f"{result.total_score:.0f}%")
    hm_html, hm_h = S.heatmap_block(model)
    legend_rows = [
        (viz.MID, "Strong \u2014 margins, cash cycle, profit growth"),
        (GREEN_DARK, "Stable \u2014 leverage within sector norms"),
        ("repeating-linear-gradient(-45deg,#d9dcd9 0 3px,#f2f3f1 3px 6px)",
         "Risk \u2014 interest cover, ROCE, cash conversion"),
    ]
    legend = "".join(
        f'<div class="legrow"><i style="background:{c}"></i><span>{t}</span></div>'
        for c, t in legend_rows)

    body = "".join([
        '<div class="pghead"><span class="pt">Sector lens</span>'
        '<span class="ps">One set of numbers, nine rule books.</span></div>',
        S.why_card(),
        f'<div class="card"><div class="slabel" style="padding-bottom:14px">'
        f'SAME NUMBERS, EVERY SECTOR RULE BOOK</div>{bars_html}</div>',
        '<div class="grid-300"><div style="display:flex;flex-direction:column;gap:14px;'
        'min-width:0">',
        '<div class="card healthrow"><div class="htxt">'
        '<div class="t">Financial health</div><div class="s">Composite index under '
        "this lens</div></div>"
        f'<div style="position:relative;width:190px;height:118px;flex:none">'
        f"{gauge_html}</div>"
        f'<div class="legendcol">{legend}</div></div>',
        f'<div class="card"><div class="ct">How the ratios move together</div>'
        f'<div class="csub">Pairwise correlation across history</div>{hm_html}</div>'
        "</div>",
        '<div class="card" style="min-width:0"><div class="ct">Applied benchmarks'
        f"</div><div class=\"csub\">{_esc(result.sector.name)}</div>"
        f"{S.bench_table(result)}</div></div>",
    ])
    # Mild under-estimate (see ratios_shell): the resizer grows the frame to the
    # real content, and under-reserving avoids the trailing empty band.
    return _doc(body, ""), min(1800, max(1200, 800 + hm_h + bars_h))


def statements_shell(model, query: str = "") -> tuple[str, int]:
    n_rows = len(S.stmt_source(model, "Income Statement", ))
    tables = _statements_tables(model, query)
    # Mild under-estimate (see ratios_shell): the resizer grows to real content.
    height = min(1700, 300 + min(max(n_rows, 6), 40) * 42)
    body = "".join([
        '<div class="card"><div class="stmttabs">'
        '<button class="stmttab on" data-tab="is">Income Statement</button>'
        '<button class="stmttab" data-tab="ra">Ratio Analysis</button>'
        '<button class="stmttab" data-tab="cs">Common Size</button>'
        "</div>",
        tables,
        '<div class="stmtfoot"><div class="pcttoggle" id="pcttoggle">'
        '<span class="pctbox" id="pctbox">\u2713</span>'
        '<span class="pctlbl">Show % change</span></div>'
        '<span class="note">Above figures are in \u20b9 crores</span></div></div>',
    ])
    return _doc(body, ""), max(HEIGHTS["statements"], height)
