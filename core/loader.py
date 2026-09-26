"""
loader.py
---------
The FundaCheck "analyst at work" loading screen.

Shown whenever the app is waiting on something the reader cannot see: the LLM
writing a note, the interpretation being built, a Screener fetch. It replaces
Streamlit's generic spinner so the wait looks like the analyst thinking rather
than the app hanging.

Layout: centred. The analyst's own pixel portrait (images/analyst_pfp.png) sits
inside a thin circle; the title and the step caption sit underneath it. There is
no card around it — the circle is the frame.

The motion, in layers, all on ONE requestAnimationFrame loop inside one
sandboxed iframe, so it keeps moving while Python blocks:
  1. a comet arc orbits the thin ring, easing in speed and length,
  2. the analyst is alive: he breathes, his head follows a beat behind his
     shoulders, he blinks now and then, and a glint crosses his glasses,
  3. his head acts out the step — scans side to side while he looks figures
     up, lifts while he thinks, nods down while he writes,
  4. a small pixel badge on the ring shows the same action (magnifier,
     thought dots, pencil) and pops in when the step changes,
  5. the caption fades in letter by letter, then drifts out for the next step.

Nothing here reports progress it does not know: the step pills say WHICH step
the analyst is on, never a percentage, because the app cannot know how long a
model call will take.
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path

_PFP_PATH = Path(__file__).resolve().parents[1] / "images" / "analyst_pfp.png"

# Each phase is (caption, what the analyst is DOING). His head movement and the
# badge on the ring both follow the second value, so the reader sees him look
# something up, think it over, then write it down — the actual shape of the job.
#   "search" — he scans side to side; the badge is a pixel magnifying glass
#   "think"  — he looks up and holds; the badge is three thought dots
#   "write"  — he nods down at the page; the badge is a pixel pencil
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
html,body{margin:0;padding:0;background:transparent;overflow:hidden;
  font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.fcl{display:flex;flex-direction:column;align-items:center;text-align:center;
  padding:6px 16px 10px}
/* ---- the stage: one canvas holds the ring, the comet and the analyst ---- */
.fcl-stage{position:relative;width:184px;height:184px;flex:none}
.fcl-scene{display:block;width:184px;height:184px}
.fcl-src{display:none}
/* The action badge rides the ring at the lower right, like a status dot on an
   avatar. It pops (a small overshoot) whenever the step changes. */
.fcl-badge{position:absolute;left:131px;top:131px;width:34px;height:34px;
  border-radius:50%;background:#fff;border:1px solid #E1E8E3;
  box-shadow:0 6px 16px -6px rgba(21,32,26,.28);display:grid;place-items:center}
.fcl-badge.pop{animation:fclPop .52s cubic-bezier(.34,1.56,.64,1)}
@keyframes fclPop{from{transform:scale(.55);opacity:0}to{transform:scale(1);opacity:1}}
.fcl-prop{width:26px;height:26px;display:block;image-rendering:pixelated}
/* ---- the words, under the circle ---- */
.fcl-title{margin-top:14px;font-size:15px;font-weight:800;color:#15201A;
  letter-spacing:-.2px;line-height:20px}
.fcl-msg{margin-top:5px;min-height:18px;line-height:18px;
  font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;
  font-weight:600;color:#56605B;letter-spacing:.2px;
  transition:opacity .3s ease,filter .3s ease,transform .3s ease}
.fcl-msg.out{opacity:0;filter:blur(3px);transform:translateY(-5px)}
/* each letter arrives on its own: rises, sharpens, fades in */
.fcl-msg .c{display:inline-block;white-space:pre;opacity:0;
  animation:fclIn .55s cubic-bezier(.2,.7,.2,1) forwards}
@keyframes fclIn{from{opacity:0;transform:translateY(6px);filter:blur(5px)}
  to{opacity:1;transform:none;filter:blur(0)}}
.fcl-dots{display:inline-block;margin-left:2px}
.fcl-dots i{display:inline-block;width:3px;height:3px;margin-left:3px;
  border-radius:50%;background:#2F9E63;vertical-align:middle;
  animation:fclDot 1.2s ease-in-out infinite}
.fcl-dots i:nth-child(2){animation-delay:.16s}
.fcl-dots i:nth-child(3){animation-delay:.32s}
@keyframes fclDot{0%,100%{opacity:.25;transform:translateY(0)}
  40%{opacity:1;transform:translateY(-2px)}}
/* WHICH step he is on — never how far through the wait (the app cannot know). */
.fcl-steps{display:flex;gap:6px;margin-top:13px}
.fcl-steps i{display:block;height:4px;width:6px;border-radius:4px;background:#DDE5E0;
  transition:width .6s cubic-bezier(.65,0,.35,1),background-color .6s ease}
.fcl-steps i.done{background:#A9D3BB}
.fcl-steps i.on{width:22px;background:#2F9E63}
/* Honour a reader who has asked the OS for less motion: the loader still shows
   and still says what it is doing, it just stops moving. */
@media (prefers-reduced-motion:reduce){
  .fcl-msg,.fcl-steps i{transition:none}
  .fcl-msg .c{animation:none;opacity:1}
  .fcl-dots i,.fcl-badge.pop{animation:none}
}
"""

# All motion on ONE rAF loop, time-based (never frame-counted), so it runs at
# the same speed on a 60Hz laptop and a 120Hz phone.
_JS = """
(function(){
  var PHASES = __PHASES__, REDUCED = !!(window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  var cv    = document.querySelector('.fcl-scene');
  var src   = document.querySelector('.fcl-src');
  var pv    = document.querySelector('.fcl-prop');
  var badge = document.querySelector('.fcl-badge');
  var msg   = document.querySelector('.fcl-msg');
  var steps = document.querySelectorAll('.fcl-steps i');
  if (!cv || !msg) { return; }
  var ctx = cv.getContext('2d'), pctx = pv ? pv.getContext('2d') : null;
  var DPR = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
  var FROZEN = 1500;         /* the readable pose a reduced-motion reader sees */

  function fit(el, c, w, h){
    el.width = Math.round(w * DPR); el.height = Math.round(h * DPR);
    c.setTransform(DPR, 0, 0, DPR, 0, 0);
  }
  var S = 184, CX = S / 2, CY = S / 2;
  var R = 86;                /* the thin ring */
  var RD = 78;               /* the disc he sits in, inset from the ring */
  fit(cv, ctx, S, S);
  if (pctx) { fit(pv, pctx, 26, 26); pctx.imageSmoothingEnabled = false; }

  /* ====================================================================
     THE ANALYST — split into head and body so the head can move on its own.
     All coordinates below are in the portrait's own 736px space.
     ==================================================================== */
  var IW = 736, DW = 172;               /* drawn width of the portrait */
  var K = DW / IW;
  var CUT = 468, FEATHER = 14;          /* the neck, where head meets body */
  var EYES = [[357, 344, 28, 16], [504, 344, 30, 16]];  /* lower eye rows */
  var LENSES = [[320, 328, 98, 76], [459, 328, 102, 76]];
  var SKIN = '#D9D9D9';
  var head = null, body = null;

  /* Downscale once, in halving steps, to the exact device size: the portrait
     stays crisp, and every frame after is a cheap 1:1 draw that can move by a
     fraction of a pixel — which is what makes the motion read as smooth. */
  function layer(mask){
    var n = Math.round(DW * DPR);
    var a = document.createElement('canvas'); a.width = a.height = IW;
    var ac = a.getContext('2d');
    ac.drawImage(src, 0, 0, IW, IW);
    ac.globalCompositeOperation = 'destination-in';
    ac.fillStyle = mask(ac); ac.fillRect(0, 0, IW, IW);
    var cur = a, size = IW;
    while (size / 2 > n) {
      var h = document.createElement('canvas');
      h.width = h.height = Math.round(size / 2);
      var hc = h.getContext('2d'); hc.imageSmoothingQuality = 'high';
      hc.drawImage(cur, 0, 0, h.width, h.height);
      cur = h; size = h.width;
    }
    var o = document.createElement('canvas'); o.width = o.height = n;
    var oc = o.getContext('2d'); oc.imageSmoothingQuality = 'high';
    oc.drawImage(cur, 0, 0, n, n);
    return o;
  }
  function build(){
    try {
      /* head: solid down to the neck, then fading out over the body */
      head = layer(function(c){
        var g = c.createLinearGradient(0, CUT - FEATHER, 0, CUT + FEATHER);
        g.addColorStop(0, '#000'); g.addColorStop(1, 'rgba(0,0,0,0)'); return g; });
      /* body: nothing above the neck, so a moving head never shows a ghost */
      body = layer(function(c){
        var g = c.createLinearGradient(0, CUT - FEATHER - 1, 0, CUT - FEATHER);
        g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(1, '#000'); return g; });
    } catch (e) { head = body = null; }
    if (REDUCED) { paint(FROZEN, 0); }
  }

  /* ---- his head, per action: where it wants to be at time t ------------ */
  function headTarget(mode, t){
    if (mode === 'search') {             /* scanning the figures, side to side */
      return [Math.sin(t * 0.0017) * 2.4, 0.3 + Math.abs(Math.cos(t * 0.0017)) * -0.6];
    }
    if (mode === 'write') {              /* small nods down at the page */
      var nod = Math.pow(Math.max(0, Math.sin(t * 0.0085)), 2);
      return [-0.7, 1.3 + nod * 1.1];
    }
    return [1.1 + Math.sin(t * 0.0011) * 0.5, -1.7];   /* think: looking up */
  }
  var hx = 0, hy = 0;                    /* eased head offset, in CSS px */

  /* ---- blinking: at random, sometimes twice -------------------------------- */
  var nextBlink = 1800, blinkAt = -1e9;
  function blinkAmount(t){
    if (t > nextBlink) {
      blinkAt = t;
      nextBlink = t + (Math.random() < 0.22 ? 260 : 2600 + Math.random() * 2800);
    }
    var d = t - blinkAt;
    return d < 150 ? Math.sin(d / 150 * Math.PI) : 0;
  }

  /* ====================================================================
     A FRAME
     ==================================================================== */
  var mode = PHASES[0][1];
  function paint(t, dt){
    ctx.clearRect(0, 0, S, S);
    var breath = Math.sin(t / 3600 * Math.PI * 2);        /* one breath, 3.6s */
    var lag    = Math.sin(t / 3600 * Math.PI * 2 - 0.6);  /* head follows */

    /* the soft halo breathes with him */
    var halo = ctx.createRadialGradient(CX, CY, RD - 6, CX, CY, R + 8);
    halo.addColorStop(0, 'rgba(47,158,99,0)');
    halo.addColorStop(0.55, 'rgba(47,158,99,' + (0.07 + 0.04 * breath).toFixed(3) + ')');
    halo.addColorStop(1, 'rgba(47,158,99,0)');
    ctx.fillStyle = halo;
    ctx.beginPath(); ctx.arc(CX, CY, R + 8, 0, Math.PI * 2); ctx.fill();

    /* the disc, and him inside it */
    ctx.save();
    ctx.beginPath(); ctx.arc(CX, CY, RD, 0, Math.PI * 2); ctx.clip();
    ctx.fillStyle = '#FFFFFF'; ctx.fillRect(0, 0, S, S);
    var bg = ctx.createLinearGradient(0, CY - RD, 0, CY + RD);
    bg.addColorStop(0, 'rgba(238,244,240,0)'); bg.addColorStop(1, 'rgba(226,238,230,.9)');
    ctx.fillStyle = bg; ctx.fillRect(0, 0, S, S);

    var ix = CX - DW / 2, iy = CY - RD + 2;
    var bodyY = -breath * 0.9;
    if (body && head) {
      var tg = headTarget(mode, t);
      var ease = dt ? 1 - Math.exp(-dt / 200) : 1;  /* glide between actions */
      hx += (tg[0] - hx) * ease; hy += (tg[1] - hy) * ease;
      var HX = ix + hx, HY = iy + bodyY + hy - lag * 0.7;
      ctx.drawImage(body, ix, iy + bodyY, DW, DW);
      ctx.drawImage(head, HX, HY, DW, DW);

      /* head-space drawing: blink and glint move with him */
      ctx.save();
      ctx.translate(HX, HY); ctx.scale(K, K);
      var b = REDUCED ? 0 : blinkAmount(t);
      if (b > 0.2) {
        ctx.fillStyle = SKIN;
        for (var e = 0; e < EYES.length; e++) {
          var E = EYES[e], cover = b > 0.6 ? E[3] : E[3] / 2;
          ctx.fillRect(E[0], E[1] + E[3] - cover, E[2], cover);
        }
      }
      var gp = ((t + 900) % 4600) / 700;           /* a glint every 4.6s */
      if (gp < 1 && !REDUCED) {
        var s = gp * gp * (3 - 2 * gp);
        var gx = 250 + s * 360;
        ctx.beginPath();
        for (var l = 0; l < LENSES.length; l++) {
          var L = LENSES[l]; ctx.rect(L[0], L[1], L[2], L[3]);
        }
        ctx.clip();
        ctx.fillStyle = 'rgba(255,255,255,' + (0.55 * Math.sin(s * Math.PI)).toFixed(3) + ')';
        ctx.beginPath();
        ctx.moveTo(gx, 320); ctx.lineTo(gx + 26, 320);
        ctx.lineTo(gx - 14, 410); ctx.lineTo(gx - 40, 410); ctx.closePath();
        ctx.fill();
      }
      ctx.restore();
    } else {
      ctx.fillStyle = '#E9ECE8';
      ctx.beginPath(); ctx.arc(CX, CY + 10, RD * 0.55, 0, Math.PI * 2); ctx.fill();
    }
    ctx.restore();

    /* a hairline on the disc edge, so white on white still reads as a disc */
    ctx.strokeStyle = 'rgba(21,32,26,.07)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(CX, CY, RD, 0, Math.PI * 2); ctx.stroke();

    /* ---- the thin ring and its comet ---------------------------------- */
    ctx.strokeStyle = '#DCE4DE'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(CX, CY, R, 0, Math.PI * 2); ctx.stroke();

    var A = -Math.PI / 2 + t * 0.0021 + Math.sin(t * 0.0012) * 0.45;  /* head */
    var LEN = Math.PI * (0.32 + 0.38 * (0.5 - 0.5 * Math.cos(t * 0.0013)));
    var N = 36;
    ctx.lineCap = 'butt';
    for (var i = 0; i < N; i++) {
      var f = (i + 1) / N;
      ctx.strokeStyle = 'rgba(47,158,99,' + Math.pow(f, 1.7).toFixed(3) + ')';
      ctx.lineWidth = 1 + 1.4 * f;
      ctx.beginPath();
      ctx.arc(CX, CY, R, A - LEN * (1 - i / N), A - LEN * (1 - f) + 0.004);
      ctx.stroke();
    }
    var dx = CX + Math.cos(A) * R, dy = CY + Math.sin(A) * R;
    ctx.save();
    ctx.shadowColor = 'rgba(47,158,99,.75)'; ctx.shadowBlur = 10;
    ctx.fillStyle = '#2F9E63';
    ctx.beginPath(); ctx.arc(dx, dy, 2.6, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
    ctx.fillStyle = '#FFFFFF';
    ctx.beginPath(); ctx.arc(dx, dy, 1, 0, Math.PI * 2); ctx.fill();

    drawProp(t);
  }

  /* ====================================================================
     THE BADGE — tiny pixel props, snapped to a 2px grid like the portrait.
     ==================================================================== */
  var G = 2, INK = '#15201A', LINE = '#3F4744', GREEN = '#2F9E63';
  function blk(c, x, y, w, h, col){
    c.fillStyle = col;
    c.fillRect(Math.round(x / G) * G, Math.round(y / G) * G,
               Math.max(G, Math.round(w / G) * G), Math.max(G, Math.round(h / G) * G));
  }
  /* a magnifying glass circling slowly, as if hunting across a page */
  function drawSearch(c, t){
    var cx = 11 + Math.cos(t * 0.004) * 2, cy = 11 + Math.sin(t * 0.004) * 2, r = 6;
    for (var k = 0; k < 4; k++) { blk(c, cx + 4 + k * 2, cy + 4 + k * 2, 3, 3, LINE); }
    c.fillStyle = 'rgba(47,158,99,.18)';
    c.beginPath(); c.arc(cx, cy, r - 1, 0, Math.PI * 2); c.fill();
    for (var a = 0; a < 20; a++) {
      var ang = a / 20 * Math.PI * 2;
      blk(c, cx + Math.cos(ang) * r - 1, cy + Math.sin(ang) * r - 1, 2, 2, INK);
    }
    blk(c, cx - 3, cy - 3, 2, 2, '#FFFFFF');
  }
  /* three thought dots, lighting up in turn and hopping as they do */
  function drawThink(c, t){
    var lit = Math.floor((t % 1800) / 450);
    for (var i = 0; i < 3; i++) {
      var on = lit === i;
      blk(c, 5 + i * 6, on ? 10 : 12, 4, 4, on ? GREEN : (lit > i ? '#8FC7A6' : '#D3DDD6'));
    }
  }
  /* a pencil ruling a line, then lifting and starting again */
  function drawWrite(c, t){
    var p = (t % 1600) / 1600, run = Math.min(1, p / 0.8);
    blk(c, 4, 20, 16 * run, 2, LINE);
    var tx = 4 + 16 * run, ty = 18 - (p > 0.8 ? (p - 0.8) * 20 : 0);
    blk(c, tx, ty, 2, 2, '#C9803A');
    for (var s = 1; s < 6; s++) {
      blk(c, tx + s * 2, ty - s * 2, 3, 3, s > 4 ? GREEN : '#D9A441');
    }
  }
  var PROPS = { search: drawSearch, think: drawThink, write: drawWrite };
  function drawProp(t){
    if (!pctx) { return; }
    pctx.clearRect(0, 0, 26, 26);
    (PROPS[mode] || drawThink)(pctx, REDUCED ? FROZEN : t);
  }

  /* ====================================================================
     THE WORDS — each step's caption fades in letter by letter, holds, then
     drifts out; the badge, the pills and his head change with it.
     ==================================================================== */
  var phase = 0;
  function show(i){
    var step = PHASES[i % PHASES.length], text = step[0];
    mode = step[1];
    msg.textContent = '';
    for (var k = 0; k < text.length; k++) {
      var sp = document.createElement('span');
      sp.className = 'c'; sp.textContent = text[k];
      sp.style.animationDelay = (k * 20) + 'ms';
      msg.appendChild(sp);
    }
    var dots = document.createElement('span');
    dots.className = 'fcl-dots';
    dots.innerHTML = '<i></i><i></i><i></i>';
    msg.appendChild(dots);
    msg.classList.remove('out');
    for (var s = 0; s < steps.length; s++) {
      var at = i % PHASES.length;
      steps[s].className = s === at ? 'on' : (s < at ? 'done' : '');
    }
    if (badge) { badge.classList.remove('pop'); void badge.offsetWidth; badge.classList.add('pop'); }
    return text.length * 20 + 550 + 2400;          /* reveal, then hold */
  }
  function cycle(){
    var hold = show(phase);
    setTimeout(function(){
      msg.classList.add('out');
      setTimeout(function(){ phase++; cycle(); }, 320);
    }, hold);
  }

  var start = 0, last = 0, raf = null;
  function frame(now){
    if (!start) { start = now - last; }
    var t = now - start, dt = Math.min(64, t - last); last = t;
    paint(t, dt);
    raf = requestAnimationFrame(frame);
  }

  /* Split the portrait once it has decoded (it is a data URI, so usually at
     once). Kicked off last, so everything build() may call already exists. */
  if (src && src.getAttribute('src')) {
    if (src.complete && src.naturalWidth) { build(); }
    else { src.onload = build; }
  }

  if (REDUCED) {
    /* Frozen scene, captions swapped whole and slowly: less movement, never
       less information. */
    show(0); paint(FROZEN, 0);
    setInterval(function(){ phase++; show(phase); paint(FROZEN, 0); }, 3000);
  } else {
    cycle();
    raf = requestAnimationFrame(frame);
    /* Stop burning frames if the tab is hidden, pick up where it left off. */
    document.addEventListener('visibilitychange', function(){
      if (document.hidden) { if (raf) { cancelAnimationFrame(raf); raf = null; } }
      else if (!raf) { start = 0; raf = requestAnimationFrame(frame); }
    });
  }
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
    """A complete, self-contained HTML document for the loading screen.

    `tag` is kept for callers that pass it; it is now the accessible label
    rather than a visible chip."""
    steps = _normalise(phases)
    uri = _pfp_uri()
    # json.dumps does not escape "</", which would close the <script> early.
    script = _JS.replace("__PHASES__", json.dumps(steps).replace("</", "<\\/"))
    pills = "".join('<i class="on"></i>' if i == 0 else "<i></i>"
                    for i in range(len(steps)))
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>{_CSS}</style></head><body>
<div class="fcl" role="status" aria-live="polite" aria-label="{html.escape(str(tag))}">
  <div class="fcl-stage">
    <img class="fcl-src" alt="" src="{uri}">
    <canvas class="fcl-scene"></canvas>
    <div class="fcl-badge"><canvas class="fcl-prop"></canvas></div>
  </div>
  <div class="fcl-title">{html.escape(str(title))}</div>
  <div class="fcl-msg">{html.escape(steps[0][0])}</div>
  <div class="fcl-steps">{pills}</div>
</div>
<script>{script}</script>
</body></html>"""


# The height the iframe needs, in CSS pixels. Fixed, because Streamlit reserves
# this much room while the loader is on screen.
LOADER_HEIGHT = 292
