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
  3. he acts out the step HIMSELF, drawn on his own pixel grid — no icons:
       search  holds a big magnifying glass up and sweeps it across his
               glasses (the lens really magnifies him), eyes following it,
       think   scratches his head, eyes up and away, "hmm",
               "hmm" ... then an "oh!" and a grin,
       write   types on a laptop (seen from behind the lid, the screen
               lighting his face), eyes running along the lines, then
               looks up, pleased,
     and his hands rise into frame on a spring as one step hands to the next,
  4. the caption fades in letter by letter, then drifts out for the next step.

It always animates, even when the OS asks for reduced motion: Windows sets
that by default on many machines, and freezing him turned the character into
three still pictures.

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

# Each phase is (caption, what the analyst is DOING). He acts the second value
# out himself, so the reader sees him look something up, think it over, then
# write it down — the actual shape of the job.
#   "search" — he peers through a magnifying glass, sweeping it side to side
#   "think"  — scratches his head, eyes up, "hmm", then "aha"
#   "write"  — he types on a laptop, eyes running along the lines
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
    "sector": (
        ("Finding the company's peers", SEARCH),
        ("Reading where the sector's cycle stands", THINK),
        ("Laying out the sector lens", WRITE),
    ),
}

# The welcome screen: before any upload he demonstrates the three things he
# will do, and the matching step card lights up as he does each one.
WELCOME_PHASES: tuple[tuple[str, str], ...] = (
    ("I read your statements", SEARCH),
    ("I weigh them against the sector", THINK),
    ("I write you the verdict", WRITE),
)

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
  padding:6px 16px 10px;animation:fclAppear .45s cubic-bezier(.2,.7,.2,1) .25s both}
/* Held back a beat: a job that finishes at once (cached) never flashes it. */
@keyframes fclAppear{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
/* ---- the stage: one canvas holds the ring, the comet and the analyst ---- */
.fcl-stage{position:relative;width:184px;height:184px;flex:none}
.fcl-scene{display:block;width:184px;height:184px}
.fcl-src{display:none}
/* ---- the words, under the circle ---- */
.fcl-title{margin-top:14px;font-size:15px;font-weight:800;color:#15201A;
  letter-spacing:-.2px;line-height:20px}
.fcl-msg{margin-top:5px;min-height:18px;line-height:18px;
  font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;
  font-weight:600;color:#56605B;letter-spacing:.2px;
  transition:opacity .3s ease,filter .3s ease,transform .3s ease}
.fcl-msg.out{opacity:0;filter:blur(3px);transform:translateY(-5px)}
/* each letter arrives on its own: rises, sharpens, fades in */
.fcl-msg .w{display:inline-block;white-space:nowrap}
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
"""

# All motion on ONE rAF loop, time-based (never frame-counted), so it runs at
# the same speed on a 60Hz laptop and a 120Hz phone.
_JS = """
(function(){
  /* Always animated, by the owner's choice. Windows turns the OS "reduce
     motion" setting on by default on many machines, and honouring it froze
     him into three still pictures — the opposite of the point. */
  var PHASES = __PHASES__;

  var cv    = document.querySelector('.fcl-scene');
  var src   = document.querySelector('.fcl-src');
  var msg   = document.querySelector('.fcl-msg');
  var steps = document.querySelectorAll('[data-step]');
  if (!cv || !msg) { return; }
  var ctx = cv.getContext('2d');
  var DPR = Math.max(1, Math.min(3, window.devicePixelRatio || 1));

  var S = 184, CX = S / 2, CY = S / 2;
  var R = 86;                /* the thin ring */
  var RD = 78;               /* the disc he sits in, inset from the ring */
  cv.width = cv.height = Math.round(S * DPR);
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);

  /* ====================================================================
     THE ANALYST — split into head and body so the head can move on its own.
     Every coordinate below is in the portrait's own 736px space, where one
     of his pixels (C) is ~14.7 units wide. Hands, props and new faces are
     drawn on that same grid, so they look like part of the original art.
     ==================================================================== */
  var IW = 736, DW = 172, K = DW / IW, C = 14.72;
  var CUT = 468, FEATHER = 14;          /* the neck, where head meets body */
  var INK = '#141414', DARK = '#2E2E2E', MID = '#707070', GRAY = '#B8B8B8',
      SKIN = '#D9D9D9', SHADE = '#B2B2B2', WHITE = '#EDEDED',
      PAPER = '#FBFBF8', GREEN = '#2F9E63';
  var head = null, body = null, hf = null, hfc = null, NPX = 0;

  /* Downscale once, in halving steps, to the exact device size: the portrait
     stays crisp, and every frame after is a cheap draw that can move by a
     fraction of a pixel — which is what makes the motion read as smooth. */
  function layer(mask){
    var a = document.createElement('canvas'); a.width = a.height = IW;
    var ac = a.getContext('2d');
    ac.drawImage(src, 0, 0, IW, IW);
    ac.globalCompositeOperation = 'destination-in';
    ac.fillStyle = mask(ac); ac.fillRect(0, 0, IW, IW);
    var cur = a, size = IW;
    while (size / 2 > NPX) {
      var h = document.createElement('canvas');
      h.width = h.height = Math.round(size / 2);
      var hc = h.getContext('2d'); hc.imageSmoothingQuality = 'high';
      hc.drawImage(cur, 0, 0, h.width, h.height);
      cur = h; size = h.width;
    }
    var o = document.createElement('canvas'); o.width = o.height = NPX;
    var oc = o.getContext('2d'); oc.imageSmoothingQuality = 'high';
    oc.drawImage(cur, 0, 0, NPX, NPX);
    return o;
  }
  function build(){
    NPX = Math.round(DW * DPR);
    try {
      /* head: solid down to the neck, then fading out over the body */
      head = layer(function(c){
        var g = c.createLinearGradient(0, CUT - FEATHER, 0, CUT + FEATHER);
        g.addColorStop(0, '#000'); g.addColorStop(1, 'rgba(0,0,0,0)'); return g; });
      /* body: nothing above the neck, so a moving head never shows a ghost */
      body = layer(function(c){
        /* starts well under the head, so a head that lifts never opens a gap */
        var g = c.createLinearGradient(0, CUT - FEATHER - 24, 0, CUT - FEATHER - 23);
        g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(1, '#000'); return g; });
      /* the head as it looks THIS frame — with his current eyes and mouth */
      hf = document.createElement('canvas'); hf.width = hf.height = NPX;
      hfc = hf.getContext('2d');
    } catch (e) { head = body = null; }
  }

  function rect(c, x, y, w, h, col){ c.fillStyle = col; c.fillRect(x, y, w, h); }

  /* ---- his face ---------------------------------------------------------
     His eyes are 2x2 pixel blocks behind the glasses, hanging from his hair.
     Each lens is repainted (shadow row, skin, the white glare) and the eye is
     put back where he is looking: one pixel left or right, down at a page,
     up under his fringe, or shut. */
  var LENS = [
    { x0: 328, x1: 416, low: 328, glare: [332, 345, 21, 14], eye: 356 },
    { x0: 476, x1: 532, low: 492, glare: [476, 345, 25, 14], eye: 504 }
  ];
  var LENSES = [[320, 328, 98, 76], [459, 328, 102, 76]];   /* glint areas */
  /* Where each eye shape sits: [top, bottom] of the eye block. */
  var EYE = { open: [331, 359], up: [331, 345], down: [345, 374], closed: [349, 358] };
  /* The eye GLIDES to where he is looking — its position and its top and
     bottom edges all ease — so he looks around rather than jumping between
     stills. A blink closes faster than a glance. */
  var eye = { x: 0, top: 331, bot: 359 };
  function moveEyes(a, dt){
    var g = EYE[a.eye] || EYE.open;
    var k = dt ? 1 - Math.exp(-dt / (a.eye === 'closed' ? 22 : 75)) : 1;
    eye.x += (a.ex * 14 - eye.x) * k;
    eye.top += (g[0] - eye.top) * k;
    eye.bot += (g[1] - eye.bot) * k;
  }
  function drawEyes(c){
    for (var i = 0; i < LENS.length; i++) {
      var L = LENS[i];
      rect(c, L.x0, 331, L.x1 - L.x0, 14, GRAY);
      rect(c, L.x0, 345, L.x1 - L.x0, 14, SKIN);
      rect(c, L.low, 359, L.x1 - L.low, 15, SKIN);
      rect(c, L.glare[0], L.glare[1], L.glare[2], L.glare[3], WHITE);
      rect(c, L.eye + eye.x, eye.top, 28, Math.max(6, eye.bot - eye.top), INK);
    }
  }
  /* His mouth: the original flat line, or a smile, a grin, an "oh!", a
     sideways "hmm", or a small pursed line while he concentrates. */
  function drawMouth(c, m){
    if (m === 'flat') { return; }
    rect(c, 404, 440, 76, 36, SKIN);
    if (m === 'smile') {
      rect(c, 409, 443, 14, 8, INK); rect(c, 453, 443, 14, 8, INK);
      rect(c, 420, 451, 36, 8, INK);
    } else if (m === 'grin') {
      rect(c, 407, 440, 14, 8, INK); rect(c, 455, 440, 14, 8, INK);
      rect(c, 418, 447, 40, 15, INK); rect(c, 425, 450, 26, 6, '#8C8C8C');
    } else if (m === 'o') {
      rect(c, 426, 441, 26, 25, INK); rect(c, 433, 448, 12, 12, '#5C5C5C');
    } else if (m === 'hmm') {
      rect(c, 434, 450, 28, 9, INK); rect(c, 461, 445, 9, 6, INK);
    } else {                                           /* 'small' */
      rect(c, 429, 448, 20, 9, INK);
    }
  }

  /* ---- his hands and what they hold, drawn on his own pixel grid -------- */
  var PAL = { '#': INK, 'x': DARK, 'm': MID, 's': SKIN, 'S': SHADE,
              'w': WHITE, 'p': PAPER, 'g': GREEN };
  function sprite(c, rows, x, y){
    for (var r = 0; r < rows.length; r++) {
      for (var i = 0; i < rows[r].length; i++) {
        var k = rows[r].charAt(i);
        if (k !== '.') { rect(c, x + i * C, y + r * C, C + 0.6, C + 0.6, PAL[k]); }
      }
    }
  }
  var SCRATCH = [                   /* an open hand, fingers in his hair,
                                        forearm angled away from his face */
    '.#.#.#.#....', '#s#s#s#s#...', '#s#s#s#s#...', '#s#s#s#s#...',
    '#sssssss#...', '#sssssss##..', '#sSssssss#..', '#sssssss#...',
    '.#sssss#....', '..######....', '...######...', '....######..',
    '.....######.', '......######', '.......#####', '........####'];

  /* SEARCH — he holds a magnifying glass up to his glasses and sweeps it
     across, and the lens really magnifies whatever is behind it. */
  var LR = 4.9;                      /* lens radius, in his pixels */
  function drawSearch(c, lx, ly, hox, hoy){
    /* the handle runs down and out of frame — his hand is just below */
    for (var k = 0; k < 13; k++) {
      var hx0 = lx + (LR * 0.72 + k * 0.72) * C, hy0 = ly + (LR * 0.72 + k * 0.72) * C;
      rect(c, hx0 - C, hy0 - C, 2 * C, 2 * C, k < 2 ? MID : INK);
      if (k >= 2) { rect(c, hx0 - C * 0.3, hy0 - C, C * 0.6, C * 0.6, '#4A4A4A'); }
    }
    c.save();
    c.beginPath(); c.arc(lx, ly, LR * C, 0, Math.PI * 2); c.clip();
    rect(c, lx - 7 * C, ly - 7 * C, 14 * C, 14 * C, '#FFFFFF');
    c.translate(lx, ly); c.scale(1.5, 1.5); c.translate(-lx, -ly);
    c.drawImage(body, 0, 0, IW, IW);
    c.drawImage(hf, hox, hoy, IW, IW);
    c.restore();
    c.fillStyle = 'rgba(190,225,205,.18)';
    c.beginPath(); c.arc(lx, ly, LR * C, 0, Math.PI * 2); c.fill();
    for (var j = -7; j <= 7; j++) {                                  /* rim */
      for (var i = -7; i <= 7; i++) {
        var d = Math.sqrt(i * i + j * j);
        if (d > LR - 0.05 && d <= LR + 1.05) {
          rect(c, lx + i * C - C / 2, ly + j * C - C / 2, C + 0.6, C + 0.6, INK);
        }
      }
    }
    rect(c, lx - 3.5 * C, ly - 2 * C, C, 2 * C, 'rgba(255,255,255,.7)');  /* shine */
    rect(c, lx - 2.5 * C, ly - 3.5 * C, 2 * C, C, 'rgba(255,255,255,.7)');
  }

  /* THINK — scratching the side of his head; skin on black hair reads
     clearly, and nothing goes near his face. */
  function drawThink(c, tap){ sprite(c, SCRATCH, 492, 212 + tap); }

  /* WRITE — typing the report on a laptop, seen from behind the lid: the
     screen lights his face, his eyes run along the lines as he types. */
  var LX0 = 214, LY0 = 534, LW = 21;   /* the lid; LW in his pixels */
  function drawWrite(c, a, dy){
    var x = LX0, y = LY0 + dy, w = LW * C;
    rect(c, x + C, y, w - 2 * C, C, INK);                   /* rounded outline */
    rect(c, x, y + C, w, 12 * C, INK);
    rect(c, x + C, y + C, w - 2 * C, 12 * C, GRAY);
    rect(c, x + C, y + C, w - 2 * C, C, '#CBCBCB');           /* top-edge light */
    rect(c, x + C, y + 2 * C, C, 11 * C, '#A6A6A6');          /* side shade */
    var lx = x + w / 2 - C, ly = y + 4 * C;                   /* the logo */
    c.save();
    c.shadowColor = 'rgba(47,158,99,' + (0.5 + 0.3 * a.glow).toFixed(2) + ')';
    c.shadowBlur = 14;
    rect(c, lx, ly, 2 * C, 2 * C, GREEN);
    c.restore();
    rect(c, lx, ly, C, C, '#5FC08A');
  }
  /* the screen's light on his face, flickering a touch as the text moves */
  function screenGlow(c, a, p){
    var g = c.createLinearGradient(0, 520, 0, 300);
    var k = (0.16 + 0.05 * (a.glow || 0)) * p;
    g.addColorStop(0, 'rgba(120,200,160,' + k.toFixed(3) + ')');
    g.addColorStop(1, 'rgba(120,200,160,0)');
    c.fillStyle = g; c.fillRect(0, 300, IW, 240);
  }

  /* ---- what he is doing at time tm into a step --------------------------
     Returns his face (eyes + mouth), where his head leans, and where his
     hands are. Faces switch in whole pixels, like the art; the head and
     hands glide. */
  function act(mode, tm){
    var a = { ex: 0, eye: 'open', mouth: 'flat', hx: 0, hy: 0, tap: 0 };
    if (mode === 'search') {
      var per = 3800, ph = (tm % per) / per;
      var s = 0.5 - 0.5 * Math.cos(ph * Math.PI * 2);
      a.lx = 360 + s * 150; a.ly = 362 + Math.sin(tm * 0.004) * 5;
      a.ex = a.lx < 395 ? -1 : (a.lx > 475 ? 1 : 0);
      if (s > 0.93) { a.mouth = Math.floor(tm / per) % 2 ? 'smile' : 'o'; }
      a.hx = (s - 0.5) * 1.8; a.hy = 0.4;
    } else if (mode === 'write') {
      var pw = tm % 5200;
      a.glow = 0.5 + 0.5 * Math.sin(tm * 0.02);
      if (pw < 4300) {             /* typing: eyes run along each line */
        var line = (pw % 1430) / 1430;
        a.eye = 'down';
        a.ex = line < 0.34 ? -1 : (line < 0.67 ? 0 : 1);
        a.mouth = (pw % 1900) < 360 ? 'small' : 'flat';
        a.hy = 1.3 + Math.pow(Math.max(0, Math.sin(tm * 0.028)), 6) * 0.6;
      } else {                     /* a paragraph done: he looks up, pleased */
        a.mouth = 'smile'; a.hy = -0.3;
      }
      a.hx = a.ex * 0.6;
    } else {                       /* think: up-right, up-left... then "aha!" */
      var pt = tm % 4600;
      if (pt < 1700)      { a.ex = -1; a.eye = 'up'; a.mouth = 'hmm'; }
      else if (pt < 1850) { a.ex = 0;  a.eye = 'closed'; a.mouth = 'hmm'; }
      else if (pt < 3400) { a.ex = 0;  a.eye = 'up'; a.mouth = 'hmm'; }
      else if (pt < 3650) { a.mouth = 'o'; }
      else                { a.mouth = 'grin'; }
      /* scratch in short bursts while he is puzzling it out */
      a.tap = (a.mouth === 'hmm' && (tm % 900) < 500) ? Math.round(Math.sin(tm * 0.05)) * 6 : 0;
      a.hx = a.ex * 1.1; a.hy = a.eye === 'up' ? -1.6 : -0.6;
    }
    return a;
  }

  /* ---- blinking: at random, sometimes twice -------------------------------- */
  var nextBlink = 2400, blinkAt = -1e9;
  function blinking(t){
    if (t > nextBlink) {
      blinkAt = t;
      nextBlink = t + (Math.random() < 0.22 ? 260 : 2600 + Math.random() * 2800);
    }
    return t - blinkAt < 130;
  }

  /* Hands and props rise into frame and drop away on a spring (a little
     overshoot), so one action hands over to the next instead of cutting. */
  var PRESENT = { search: { p: 0, v: 0 }, think: { p: 0, v: 0 }, write: { p: 0, v: 0 } };
  function springs(dt){
    for (var k in PRESENT) {
      var s = PRESENT[k], goal = k === mode ? 1 : 0;
      if (!dt) { s.p = goal; s.v = 0; continue; }
      var h = dt / 1000;
      s.v += (170 * (goal - s.p) - 17 * s.v) * h;
      s.p += s.v * h;
    }
  }

  /* ====================================================================
     A FRAME
     ==================================================================== */
  var mode = PHASES[0][1], modeStart = 0, now = 0, hx = 0, hy = 0;
  function paint(t, dt){
    now = t;
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

    if (body && head) {
      var tm = t - modeStart;
      var a = act(mode, tm);
      if (blinking(t) && a.eye !== 'down') { a.eye = 'closed'; }

      /* this frame's head: the portrait, then his eyes and mouth on top */
      hfc.setTransform(1, 0, 0, 1, 0, 0);
      hfc.clearRect(0, 0, NPX, NPX);
      hfc.drawImage(head, 0, 0);
      hfc.setTransform(NPX / IW, 0, 0, NPX / IW, 0, 0);
      moveEyes(a, dt); drawEyes(hfc); drawMouth(hfc, a.mouth);
      if (PRESENT.write.p > 0.01) {
        hfc.globalCompositeOperation = 'source-atop';
        screenGlow(hfc, mode === 'write' ? a : { glow: 0.5 }, PRESENT.write.p);
        hfc.globalCompositeOperation = 'source-over';
      }

      var ease = dt ? 1 - Math.exp(-dt / 220) : 1;   /* glide between poses */
      hx += (a.hx - hx) * ease; hy += (a.hy - hy) * ease;
      springs(dt);

      ctx.save();
      ctx.translate(CX - DW / 2, CY - RD + 2 - breath * 0.9);
      ctx.scale(K, K);
      var hox = hx / K, hoy = (hy - lag * 0.7) / K;
      ctx.drawImage(body, 0, 0, IW, IW);
      ctx.drawImage(hf, hox, hoy, IW, IW);

      /* a glint crossing his glasses every 4.6s */
      var gp = ((t + 900) % 4600) / 700;
      if (gp < 1) {
        var sm = gp * gp * (3 - 2 * gp), gx = 250 + sm * 360;
        ctx.save();
        ctx.translate(hox, hoy);
        ctx.beginPath();
        for (var l = 0; l < LENSES.length; l++) {
          var L = LENSES[l]; ctx.rect(L[0], L[1], L[2], L[3]);
        }
        ctx.clip();
        ctx.fillStyle = 'rgba(255,255,255,' + (0.55 * Math.sin(sm * Math.PI)).toFixed(3) + ')';
        ctx.beginPath();
        ctx.moveTo(gx, 320); ctx.lineTo(gx + 26, 320);
        ctx.lineTo(gx - 14, 410); ctx.lineTo(gx - 40, 410); ctx.closePath();
        ctx.fill();
        ctx.restore();
      }

      /* his hands, each sliding up from below while it is his current job */
      var pw = PRESENT.write.p, pt = PRESENT.think.p, ps = PRESENT.search.p;
      if (pw > 0.01) {
        var aw = mode === 'write' ? a : act('write', 0);
        drawWrite(ctx, aw, (1 - pw) * 320);
      }
      if (pt > 0.01) {
        ctx.save(); ctx.translate(hox, hoy + (1 - pt) * 340);
        drawThink(ctx, mode === 'think' ? a.tap : 0);
        ctx.restore();
      }
      if (ps > 0.01) {
        var as = mode === 'search' ? a : act('search', 0);
        drawSearch(ctx, as.lx, as.ly + (1 - ps) * 420, hox, hoy);
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
  }

  /* ====================================================================
     THE WORDS — each step's caption fades in letter by letter, holds, then
     drifts out; the pills and what he is doing change with it.
     ==================================================================== */
  var phase = 0;
  function show(i){
    var step = PHASES[i % PHASES.length], text = step[0];
    if (step[1] !== mode) { mode = step[1]; modeStart = now; nextBlink = now; }
    msg.textContent = '';
    /* letters animate one by one, but each WORD stays whole when it wraps */
    var words = text.split(' '), n = 0;
    for (var w = 0; w < words.length; w++) {
      if (w) { msg.appendChild(document.createTextNode(' ')); n++; }
      var word = document.createElement('span');
      word.className = 'w';
      for (var k = 0; k < words[w].length; k++, n++) {
        var sp = document.createElement('span');
        sp.className = 'c'; sp.textContent = words[w].charAt(k);
        sp.style.animationDelay = (n * 20) + 'ms';
        word.appendChild(sp);
      }
      msg.appendChild(word);
    }
    var dots = document.createElement('span');
    dots.className = 'fcl-dots';
    dots.innerHTML = '<i></i><i></i><i></i>';
    msg.appendChild(dots);
    msg.classList.remove('out');
    var at = i % PHASES.length;
    for (var s = 0; s < steps.length; s++) {
      var n = +steps[s].getAttribute('data-step');
      steps[s].classList.toggle('on', n === at);
      steps[s].classList.toggle('done', n < at);
    }
    /* long enough to see him act the step out, not just read the words */
    return Math.max(text.length * 20 + 550 + 2400, 4600);
  }
  function cycle(){
    var hold = show(phase);
    setTimeout(function(){
      msg.classList.add('out');
      setTimeout(function(){ phase++; cycle(); }, 320);
    }, hold);
  }

  var start = 0, last = 0, raf = null;
  function frame(stamp){
    if (!start) { start = stamp - last; }
    var t = stamp - start, dt = Math.min(64, t - last); last = t;
    paint(t, dt);
    raf = requestAnimationFrame(frame);
  }

  /* Split the portrait once it has decoded (it is a data URI, so usually at
     once). Kicked off last, so everything build() may call already exists. */
  if (src && src.getAttribute('src')) {
    if (src.complete && src.naturalWidth) { build(); }
    else { src.onload = build; }
  }

  cycle();
  raf = requestAnimationFrame(frame);
  /* Stop burning frames if the tab is hidden, pick up where it left off. */
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
    """A complete, self-contained HTML document for the loading screen.

    `tag` is kept for callers that pass it; it is now the accessible label
    rather than a visible chip."""
    steps = _normalise(phases)
    uri = _pfp_uri()
    # json.dumps does not escape "</", which would close the <script> early.
    script = _JS.replace("__PHASES__", json.dumps(steps).replace("</", "<\\/"))
    pills = "".join(f'<i data-step="{i}"' + (' class="on"' if i == 0 else "") + "></i>"
                    for i in range(len(steps)))
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>{_CSS}</style></head><body>
<div class="fcl" role="status" aria-live="polite" aria-label="{html.escape(str(tag))}">
  <div class="fcl-stage">
    <img class="fcl-src" alt="" src="{uri}">
    <canvas class="fcl-scene"></canvas>
  </div>
  <div class="fcl-title">{html.escape(str(title))}</div>
  <div class="fcl-msg">{html.escape(steps[0][0])}</div>
  <div class="fcl-steps">{pills}</div>
</div>
<script>{script}</script>
</body></html>"""


_WELCOME_CSS = """
.wc{max-width:860px;margin:0 auto;padding:8px 16px 18px;text-align:center;
  animation:fclAppear .6s cubic-bezier(.2,.7,.2,1) both}
.wc .fcl{padding:0;animation:none}
.wc-kicker{margin-top:12px;font-family:ui-monospace,Menlo,Consolas,monospace;
  font-size:10.5px;font-weight:700;letter-spacing:1.6px;text-transform:uppercase;
  color:#2F9E63}
.wc h1{margin:8px 0 0;font-size:26px;line-height:1.2;font-weight:800;
  letter-spacing:-.6px;color:#15201A}
.wc-lede{margin:10px auto 0;max-width:520px;font-size:14px;line-height:1.6;color:#6B736F}
.wc-lede b{color:#3F4744;font-weight:700}
.wc .fcl-msg{margin-top:12px;color:#2F6B4A}
.wc-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:22px;
  text-align:left}
.wc-card{position:relative;background:#FFFFFF;border:1px solid #E3E9E5;border-radius:16px;
  padding:16px 16px 15px;overflow:hidden;
  transition:border-color .5s ease,box-shadow .5s ease,transform .5s cubic-bezier(.2,.7,.2,1)}
.wc-card .n{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;font-weight:700;
  letter-spacing:1px;color:#A3ABA6;transition:color .5s ease}
.wc-card .t{margin-top:6px;font-size:15px;font-weight:800;color:#15201A;letter-spacing:-.2px}
.wc-card .d{margin-top:4px;font-size:12.5px;line-height:1.5;color:#7A827E}
/* the card for what he is doing right now */
.wc-card.on{border-color:#A9D3BB;transform:translateY(-2px);
  box-shadow:0 12px 26px -16px rgba(21,80,50,.45)}
.wc-card.on .n{color:#2F9E63}
.wc-card::after{content:"";position:absolute;left:0;bottom:0;height:3px;width:0;
  background:#2F9E63;border-radius:0 3px 3px 0}
.wc-card.on::after{animation:wcFill 4.2s linear forwards}
@keyframes wcFill{to{width:100%}}
.wc-note{display:inline-flex;align-items:center;gap:8px;margin-top:18px;
  font-size:12px;color:#8B918E}
.wc-note svg{flex:none}
@media (max-width:640px){
  .wc-cards{grid-template-columns:1fr}
  .wc h1{font-size:22px}
}
"""

_WELCOME_CARDS = (
    ("01", "Upload the workbook",
     "A Screener.in-style .xlsx with a <b>HistoricalFS</b> sheet &mdash; a "
     "<b>Ratio Analysis</b> sheet helps."),
    ("02", "Pick the sector",
     "It sets the benchmarks every ratio is scored against."),
    ("03", "Read the verdict",
     "Scores, a plain-English analyst note and the sector lens."),
)


def welcome_html() -> str:
    """The first screen, before any upload: the analyst in his ring acting out
    the three things he will do, with the matching step card lit as he does it."""
    steps = _normalise(WELCOME_PHASES)
    uri = _pfp_uri()
    script = _JS.replace("__PHASES__", json.dumps(steps).replace("</", "<\\/"))
    cards = "".join(
        f'<div class="wc-card{" on" if i == 0 else ""}" data-step="{i}">'
        f'<div class="n">{n}</div><div class="t">{t}</div><div class="d">{d}</div></div>'
        for i, (n, t, d) in enumerate(_WELCOME_CARDS))
    lock = ('<svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true">'
            '<rect x="3" y="7" width="10" height="7" rx="1.5" fill="none" '
            'stroke="#8B918E" stroke-width="1.5"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" '
            'fill="none" stroke="#8B918E" stroke-width="1.5"/></svg>')
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>{_CSS}{_WELCOME_CSS}</style></head><body>
<div class="wc" id="shell">
  <div class="fcl" role="status" aria-live="polite" aria-label="Your analyst">
    <div class="fcl-stage">
      <img class="fcl-src" alt="" src="{uri}">
      <canvas class="fcl-scene"></canvas>
    </div>
  </div>
  <div class="wc-kicker">Your analyst is ready</div>
  <h1>Drop a 3-statement model into the sidebar</h1>
  <p class="wc-lede">He reads it, scores it against its sector and writes up
    the verdict &mdash; in plain words.</p>
  <div class="fcl-msg">{html.escape(steps[0][0])}</div>
  <div class="wc-cards">{cards}</div>
  <div class="wc-note">{lock}Your file is read in memory for this session only.</div>
</div>
<script>{script}</script>
</body></html>"""


WELCOME_HEIGHT = 560

# The height the iframe needs, in CSS pixels. Fixed, because Streamlit reserves
# this much room while the loader is on screen.
LOADER_HEIGHT = 292
