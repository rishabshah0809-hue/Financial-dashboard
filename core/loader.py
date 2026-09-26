"""
loader.py
---------
The FundaCheck "analyst at work" loading screen.

Shown whenever the app is waiting on something the reader cannot see: the LLM
writing a note, the interpretation being built, a Screener fetch. It replaces
Streamlit's generic spinner so the wait looks like the analyst thinking rather
than the app hanging.

Design: pixel art, to match the analyst's own avatar (images/analyst_pfp.png —
a monochrome pixel portrait). Everything moves on a single requestAnimationFrame
loop inside one sandboxed iframe, so it keeps animating while Python blocks.

The motion, in layers:
  1. the avatar idles with a 1-pixel bob and a glint that sweeps the glasses,
  2. a pixel thought-bubble fills and empties above it,
  3. a 14-bar pixel chart "computes" underneath, with a scan line sweeping it,
  4. the caption types itself out, blinks a block cursor, and cycles phases.

Nothing here reports progress it does not know: the bar is a marching pattern,
never a percentage, because the app cannot know how long a model call will take.
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path

_PFP_PATH = Path(__file__).resolve().parents[1] / "images" / "analyst_pfp.png"

# Each phase is (caption, what the analyst is DOING). The prop drawn beside the
# avatar follows the second value, so the reader sees him look something up,
# think it over, then write it down — the actual shape of the job.
#   "search" — a pixel magnifying glass sweeps across him
#   "think"  — a pixel thought bubble fills above him
#   "write"  — a pixel pencil rules lines on a small page beside him
SEARCH, THINK, WRITE = "search", "think", "write"

PHASES: dict[str, tuple[tuple[str, str], ...]] = {
    "note": (
        ("Reading the scored ratios", SEARCH),
        ("Weighing them against the sector", THINK),
        ("Writing the analyst report", WRITE),
    ),
    "interpretation": (
        ("Reading the financial model", SEARCH),
        ("Tracing margins, returns and cash", THINK),
        ("Putting it in plain words", WRITE),
    ),
    "question": (
        ("Reading your question", SEARCH),
        ("Checking it against the model", THINK),
        ("Writing the answer", WRITE),
    ),
    "fetch": (
        ("Fetching the latest data", SEARCH),
        ("Matching it to the company", SEARCH),
        ("Refreshing the figures", THINK),
    ),
    "news": (
        ("Fetching company news", SEARCH),
        ("Filtering to what matters", THINK),
        ("Summarising the headlines", WRITE),
    ),
}

DEFAULT_PHASES: tuple[tuple[str, str], ...] = (
    ("Working on it", THINK),
    ("Almost there", WRITE),
)


def _pfp_uri() -> str:
    """The analyst avatar as a data URI (survives the Streamlit asset boundary)."""
    try:
        return "data:image/png;base64," + base64.b64encode(
            _PFP_PATH.read_bytes()).decode("ascii")
    except OSError:
        return ""


_CSS = """
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:transparent;
  font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.fcl{display:flex;align-items:center;gap:20px;background:#fff;
  border:1px solid #E6EBE7;border-radius:18px;padding:16px 20px;
  box-shadow:0 1px 2px rgba(21,32,26,.04),0 10px 30px -16px rgba(21,32,26,.20)}
/* ---- the scene: the analyst on the left, room for his prop on the right ----
   It is one box rather than a bare avatar, so a magnifying glass or a pencil
   has somewhere to live without spilling over the caption or the card edge. */
.fcl-av{position:relative;width:110px;height:84px;flex:none}
.fcl-glow{position:absolute;left:-6px;top:4px;width:76px;height:76px;
  border-radius:50%;
  background:radial-gradient(circle,rgba(47,158,99,.30) 0%,rgba(47,158,99,.09) 48%,transparent 72%);
  filter:blur(7px);z-index:1}
.fcl-face{position:absolute;z-index:2;left:0;top:10px;width:64px;height:64px;
  object-fit:contain;image-rendering:pixelated;border-radius:50%;background:#fff;
  display:block}
/* the glint that sweeps the glasses — a hard-edged pixel band, no soft blur */
.fcl-glint{position:absolute;z-index:3;top:38px;left:0;width:13px;height:8px;
  background:rgba(255,255,255,.85);mix-blend-mode:screen;pointer-events:none;
  border-radius:1px;opacity:0}
/* The prop layer covers the whole scene, so a sweep can pass right over him. */
.fcl-prop{position:absolute;z-index:4;inset:0;width:110px;height:84px;
  image-rendering:pixelated;pointer-events:none}
/* ---- body column ---- */
.fcl-body{flex:1;min-width:0;display:flex;flex-direction:column;gap:9px}
.fcl-top{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap}
.fcl-title{font-size:14px;font-weight:800;color:#15201A;letter-spacing:-.2px}
.fcl-tag{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:8.5px;
  font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1E6B43;
  background:#EEF4F0;border:1px solid #CFE2D7;border-radius:20px;padding:3px 8px}
.fcl-msg{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;
  color:#3F4744;min-height:18px;letter-spacing:.2px;font-weight:600}
.fcl-cur{display:inline-block;width:7px;height:12px;background:#2F9E63;
  vertical-align:-2px;margin-left:2px;animation:fclBlink 1s steps(1,end) infinite}
@keyframes fclBlink{0%,50%{opacity:1}50.01%,100%{opacity:0}}
/* The chart is texture, not the subject — it sits quiet behind the caption. */
.fcl-bars{display:block;image-rendering:pixelated;width:100%;height:24px;opacity:.9}
/* A MARCHING pattern, never a percentage — the app cannot know how long a
   model call will take, so it must not imply it does. */
.fcl-prog{height:4px;background:#EEF1EF;overflow:hidden;image-rendering:pixelated;
  opacity:.75}
.fcl-prog i{display:block;height:100%;width:200%;
  background:repeating-linear-gradient(90deg,#2F9E63 0 8px,transparent 8px 20px);
  animation:fclMarch 1.1s linear infinite}
@keyframes fclMarch{from{transform:translateX(0)}to{transform:translateX(-20px)}}
@media (max-width:520px){
  .fcl{gap:14px;padding:13px 15px}
  .fcl-av{width:56px;height:56px}
}
/* Honour a reader who has asked the OS for less motion: the loader still shows
   and still says what it is doing, it just stops moving. */
@media (prefers-reduced-motion:reduce){
  .fcl-glint,.fcl-dot{display:none}
  .fcl-cur,.fcl-prog i{animation:none}
}
"""

# All motion on ONE rAF loop. Sizes are read from the canvas's own box so the
# bars stay crisp on any device pixel ratio, and everything is integer-snapped
# so the pixel look never turns blurry.
_JS = """
(function(){
  var PHASES = __PHASES__, REDUCED = !!(window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  var face  = document.querySelector('.fcl-face');
  var glint = document.querySelector('.fcl-glint');
  var msg   = document.querySelector('.fcl-msg');
  var cv    = document.querySelector('.fcl-bars');
  var pv    = document.querySelector('.fcl-prop');
  if (!cv || !msg) { return; }
  var ctx = cv.getContext('2d');
  var pctx = pv ? pv.getContext('2d') : null;

  /* ====================================================================
     PROPS — what the analyst is doing, drawn as pixel blocks beside him.
     Everything snaps to a 2px grid so it matches the avatar's own pixel
     size; nothing is ever drawn on a half pixel.
     ==================================================================== */
  var G = 2;                                   /* one sprite pixel */
  function blk(c, x, y, w, h, col){            /* grid-snapped block */
    c.fillStyle = col;
    c.fillRect(Math.round(x / G) * G, Math.round(y / G) * G,
               Math.max(G, Math.round(w / G) * G),
               Math.max(G, Math.round(h / G) * G));
  }

  var INK = '#15201A', LINE = '#3F4744', GREEN = '#2F9E63', PAPER = '#FFFFFF';

  /* A magnifying glass sweeping across him, left to right. The ring is drawn
     block by block around a circle so it stays hard-edged. */
  function drawSearch(c, t, W, H){
    var span = (t % 2600) / 2600;                    /* one pass per 2.6s */
    var ease = 0.5 - 0.5 * Math.cos(span * Math.PI * 2);
    var cx = 18 + ease * (W - 40);
    var cy = H * 0.46 + Math.round(Math.sin(t * 0.004) * 2);
    var r = 12;
    /* handle first, so the ring sits on top of it */
    for (var k = 0; k < 9; k++) {
      blk(c, cx + r * 0.72 + k * 1.9, cy + r * 0.72 + k * 1.9, 4, 4, LINE);
    }
    /* lens fill — barely there, just enough to read as glass */
    c.fillStyle = 'rgba(47,158,99,.14)';
    c.beginPath(); c.arc(cx, cy, r - 1, 0, Math.PI * 2); c.fill();
    /* the ring */
    for (var a = 0; a < 44; a++) {
      var ang = a / 44 * Math.PI * 2;
      blk(c, cx + Math.cos(ang) * r - 1.5, cy + Math.sin(ang) * r - 1.5,
          3, 3, INK);
    }
    /* a glint block on the upper-left of the lens */
    blk(c, cx - r * 0.5, cy - r * 0.55, 4, 4, PAPER);
  }

  /* A thought bubble above his head: two trailing dots, then a cloud whose
     three pixels fill one at a time and clear — the classic "thinking" tell. */
  function drawThink(c, t, W, H){
    var bob = Math.round(Math.sin(t * 0.003) * 2);
    var bx = 78, by = 24 + bob;                      /* clear of his head */
    blk(c, 56, 52 + bob, 4, 4, '#CBD8CF');           /* trailing bubbles */
    blk(c, 62, 43 + bob, 6, 6, '#BBCCC1');
    /* the cloud: a rounded pixel blob */
    var w = 40, h = 22;
    blk(c, bx - w / 2, by - h / 2 + 4, w, h - 8, PAPER);
    blk(c, bx - w / 2 + 4, by - h / 2, w - 8, h, PAPER);
    for (var e = 0; e < 2; e++) {               /* a 1px ink outline */
      blk(c, bx - w / 2 + 4, by - h / 2 + e * (h - 2), w - 8, 2, '#D6DED9');
    }
    /* three dots, filling in sequence */
    var lit = Math.floor((t % 2000) / 500);
    for (var i = 0; i < 3; i++) {
      blk(c, bx - 13 + i * 10, by - 2, 6, 6, (lit > i) ? GREEN : '#DDE4DF');
    }
  }

  /* A pencil ruling lines on a small page: the page fills line by line, the
     pencil tracks the line it is drawing, then the page clears and repeats. */
  function drawWrite(c, t, W, H){
    var px = 62, py = 20;                        /* page top-left */
    var pw = 36, ph = 46;
    /* A faintly tinted page with a full pixel border, so it reads as paper on
       a white card rather than two rules floating in space. */
    blk(c, px, py, pw, ph, '#FAFBFA');
    blk(c, px, py, pw, 2, '#D6DED9');
    blk(c, px, py + ph - 2, pw, 2, '#D6DED9');
    blk(c, px, py, 2, ph, '#D6DED9');
    blk(c, px + pw - 2, py, 2, ph, '#D6DED9');

    var cycle = (t % 3000) / 3000;
    var LINES = 4, filled = cycle * LINES;
    for (var i = 0; i < LINES; i++) {
      var frac = Math.max(0, Math.min(1, filled - i));
      if (frac <= 0) { break; }
      blk(c, px + 5, py + 9 + i * 9, (pw - 12) * frac, 3, LINE);
    }
    /* the pencil, tipped at the line being written */
    var li = Math.min(LINES - 1, Math.floor(filled));
    var tipX = px + 5 + (pw - 12) * Math.max(0, Math.min(1, filled - li));
    var tipY = py + 9 + li * 9;
    blk(c, tipX, tipY - 1, 4, 4, '#C9803A');            /* tip */
    for (var s = 1; s < 9; s++) {                        /* shaft */
      blk(c, tipX + s * 2.6, tipY - 2 - s * 2.6, 5, 5, s > 6 ? GREEN : '#D9A441');
    }
  }

  var PROPS = { search: drawSearch, think: drawThink, write: drawWrite };

  /* A reader who asked the OS for less motion still SEES what the analyst is
     doing — the scene is simply frozen at a readable pose instead of moving.
     Reduced motion means less movement, never less information. */
  var FROZEN = 1500;

  function drawProp(t, mode){
    if (!pctx) { return; }
    var W = pv.clientWidth || 108, H = pv.clientHeight || 108;
    pctx.clearRect(0, 0, W, H);
    (PROPS[mode] || drawThink)(pctx, REDUCED ? FROZEN : t, W, H);
  }

  /* ---- pixel bar chart -------------------------------------------------
     14 bars with their own drift, so it reads as figures being worked out
     rather than a bar that fills to 100%. It is deliberately NOT a progress
     meter: the app cannot know how long a model call takes. */
  var N = 16, PX = 2;                       // PX = size of one "pixel" block
  var bars = [];
  for (var i = 0; i < N; i++) {
    bars.push({ v: 0.18 + Math.random() * 0.34,
                t: Math.random() * Math.PI * 2,
                s: 0.7 + Math.random() * 0.9 });
  }
  var DPR = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
  function fit(el, c, dw, dh){
    var w = el.clientWidth || dw, h = el.clientHeight || dh;
    el.width = Math.round(w * DPR); el.height = Math.round(h * DPR);
    c.setTransform(DPR, 0, 0, DPR, 0, 0);
    c.imageSmoothingEnabled = false;
  }
  function sizeCanvas(){
    fit(cv, ctx, 300, 24);
    if (pctx) { fit(pv, pctx, 108, 108); }
  }
  sizeCanvas();
  window.addEventListener('resize', sizeCanvas);

  function drawBars(t, scan){
    var w = cv.clientWidth || 300, h = cv.clientHeight || 34;
    ctx.clearRect(0, 0, w, h);
    var gap = 5, bw = Math.max(PX * 2, Math.floor((w - gap * (N - 1)) / N));
    var total = bw * N + gap * (N - 1);
    var x0 = Math.floor((w - total) / 2);
    for (var i = 0; i < N; i++) {
      var b = bars[i];
      var v = b.v + Math.sin(t * 0.0016 * b.s + b.t) * 0.22;
      v = Math.max(0.10, Math.min(0.95, v));
      /* snap the height to whole blocks so the bars stay pixel-art */
      var blocks = Math.max(1, Math.round((v * h) / PX));
      var bh = blocks * PX;
      var x = x0 + i * (bw + gap);
      /* the scan line lights only the bar it is passing over */
      var lit = Math.abs(x + bw / 2 - scan) < bw;
      ctx.fillStyle = lit ? '#2F9E63' : '#D8E0DA';
      ctx.fillRect(x, Math.round(h - bh), bw, bh);
      if (lit) {                              /* a brighter cap block */
        ctx.fillStyle = '#177245';
        ctx.fillRect(x, Math.round(h - bh), bw, PX);
      }
    }
  }

  /* ---- typewriter caption ----------------------------------------------
     The caption and the PROP move together: phase 0 is looked up, phase 1 is
     thought about, phase 2 is written down. */
  var phase = 0, typed = 0, holdUntil = 0, state = 'type';
  function currentMode(){ return PHASES[phase % PHASES.length][1]; }
  function caption(t){
    var full = PHASES[phase % PHASES.length][0];
    if (state === 'type') {
      if (typed < full.length) { typed++; }
      else { state = 'hold'; holdUntil = t + 1600; }
    } else if (state === 'hold') {
      if (t > holdUntil) { state = 'erase'; }
    } else {
      if (typed > 2) { typed -= 3; }
      else { typed = 0; state = 'type'; phase++; }
    }
    msg.firstChild.nodeValue =
        full.slice(0, typed) + (typed === full.length ? '\\u2026' : '');
  }

  /* ---- idle bob + glasses glint ---------------------------------------- */
  function idle(t){
    if (REDUCED) { return; }
    /* a 1-pixel bob: snapped, never a smooth float, so it reads as pixel art */
    var bob = Math.round(Math.sin(t * 0.0022) * 1.5);
    if (face) { face.style.transform = 'translateY(' + bob + 'px)'; }

    /* the glint sweeps the lenses every ~3.6s, hard-edged and stepped */
    var g = (t % 3600) / 3600;
    if (glint) {
      if (g < 0.34) {
        var step = Math.round(g / 0.34 * 12) / 12;      /* 12 discrete stops */
        glint.style.opacity = '1';
        glint.style.transform = 'translateX(' + Math.round(step * 74) + 'px)';
      } else { glint.style.opacity = '0'; }
    }
  }

  var start = 0, raf = null;
  function frame(now){
    if (!start) { start = now; }
    var t = REDUCED ? FROZEN : (now - start);
    var w = cv.clientWidth || 300;
    var scan = REDUCED ? w * 0.42 : ((t * 0.10) % (w + 60) - 30);
    drawBars(t, scan);
    drawProp(t, currentMode());
    idle(t);
    /* Frozen scene: paint it once and stop burning frames. */
    raf = REDUCED ? null : requestAnimationFrame(frame);
  }
  raf = requestAnimationFrame(frame);

  if (REDUCED) {
    /* Show each caption whole and swap it slowly — a text change is not the
       kind of movement the reduced-motion setting is asking us to stop. */
    msg.firstChild.nodeValue = PHASES[0][0];
    setInterval(function(){
      phase++;
      msg.firstChild.nodeValue = PHASES[phase % PHASES.length][0];
      if (raf === null) { start = 0; raf = requestAnimationFrame(frame); }
    }, 2500);
  } else {
    setInterval(function(){ caption(performance.now()); }, 34);
  }

  /* Stop burning frames if the tab is hidden, restart when it comes back. */
  document.addEventListener('visibilitychange', function(){
    if (document.hidden) { if (raf) { cancelAnimationFrame(raf); raf = null; } }
    else if (!raf) { start = 0; raf = requestAnimationFrame(frame); }
  });
})();
"""


def _normalise(phases) -> list[list[str]]:
    """Accept ("caption", mode) pairs or bare captions; always return pairs."""
    steps: list[list[str]] = []
    for item in phases or DEFAULT_PHASES:
        if isinstance(item, (tuple, list)):
            text, mode = item[0], (item[1] if len(item) > 1 else THINK)
        else:
            text, mode = item, THINK
        steps.append([str(text), mode if mode in (SEARCH, THINK, WRITE) else THINK])
    return steps


def loader_html(title: str = "Thinking", phases=(),
                tag: str = "Analyst at work") -> str:
    """A complete, self-contained HTML document for the loading screen."""
    steps = _normalise(phases)
    uri = _pfp_uri()
    face = (f'<img class="fcl-face" alt="" src="{uri}">' if uri
            else '<div class="fcl-face" style="background:#E9ECE8"></div>')
    script = _JS.replace("__PHASES__", json.dumps(steps))
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>{_CSS}</style></head><body>
<div class="fcl" role="status" aria-live="polite">
  <div class="fcl-av">
    <div class="fcl-glow"></div>
    {face}
    <div class="fcl-glint"></div>
    <canvas class="fcl-prop"></canvas>
  </div>
  <div class="fcl-body">
    <div class="fcl-top">
      <span class="fcl-title">{html.escape(str(title))}</span>
      <span class="fcl-tag">{html.escape(str(tag))}</span>
    </div>
    <div class="fcl-msg">{html.escape(steps[0][0])}<span class="fcl-cur"></span></div>
    <canvas class="fcl-bars"></canvas>
    <div class="fcl-prog"><i></i></div>
  </div>
</div>
<script>{script}</script>
</body></html>"""


# The height the iframe needs, in CSS pixels. Fixed, because the component is a
# single card and Streamlit reserves this much room while it is on screen.
LOADER_HEIGHT = 132
