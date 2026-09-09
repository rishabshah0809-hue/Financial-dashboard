"""
statements_view.py
------------------
Presentation for the redesigned Statements section. Renders the design in
`fundacheck_statements.html` as a self-contained Streamlit component and injects
the REAL model data produced by `core.sections.statements_payload` — no data is
hardcoded here. Data logic stays in core/sections.py; this module only formats.

The component ships exactly like the reference: a `#shell` tray with the frame-fit
script that reports its height back to Streamlit (`streamlit:setFrameHeight`).
"""

from __future__ import annotations

import json
from html import escape

from . import sections as S

# --------------------------------------------------------------------------
# Styles — the FundaCheck palette/typography from the reference, unchanged.
# --------------------------------------------------------------------------
STMT_CSS = """
:root{
  --ink:#15201A; --ink-2:#3F4744; --ink-3:#5F6663; --mute:#8B918E; --mute-2:#9AA09D;
  --panel:#FFFFFF; --line:#E6EBE7; --line-2:#F0F3F0; --tray:#E9ECE8;
  --brand:#177245;
  --pos:#2F9E63; --pos-deep:#0F5B34; --pos-tint:#EEF4F0;
  --neg:#B4483C; --neg-tint:#FBEEEC;
  --warn:#C68A2E; --warn-deep:#B5761F; --warn-tint:#FDF3E2;
  --mono:ui-monospace,Menlo,Consolas,monospace;
  --shadow:0 1px 2px rgba(21,32,26,.04),0 6px 18px rgba(21,32,26,.06);
}
*{margin:0;padding:0;box-sizing:border-box}
html{background:#D9DED9;color-scheme:light}
body{padding:6px 20px 14px;background:linear-gradient(180deg,#E7EBE7,#D9DED9);
  font-family:'Plus Jakarta Sans',system-ui,sans-serif;color:var(--ink);
  display:flex;justify-content:center;align-items:flex-start;
  font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1,"lnum" 1;
  -webkit-font-smoothing:antialiased}
#shell{width:1320px;max-width:100%;background:var(--tray);border-radius:24px;padding:18px;
  box-shadow:0 10px 30px rgba(21,32,26,.06);display:flex;flex-direction:column;gap:14px}
svg{display:block}
::selection{background:#CFE7DA;color:#0F2A1E}
:focus-visible{outline:2px solid var(--brand);outline-offset:2px;border-radius:8px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}

.phead{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;
  flex-wrap:wrap;padding:2px 6px 0}
.phead h1{font-size:29.9px;font-weight:800;letter-spacing:-.7px;line-height:1.1}
.phead .sub{font-size:14.9px;color:var(--mute);padding-top:5px;max-width:640px}
.feed{display:flex;align-items:center;gap:20px;flex-wrap:wrap;justify-content:flex-end}
.feed-i{text-align:right}
.feed-l{font-family:var(--mono);font-size:10.3px;letter-spacing:1.2px;text-transform:uppercase;
  color:var(--mute-2)}
.feed-v{font-size:14.4px;font-weight:700;color:var(--ink-2);padding-top:2px}

.deck{background:var(--panel);border:1px solid var(--line);border-radius:20px;
  box-shadow:var(--shadow);overflow:hidden}
.bar{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:14px 18px;
  border-bottom:1px solid var(--line)}
.tabs{display:flex;background:#F1F4F1;border:1px solid #E3E8E4;border-radius:14px;padding:3px;
  gap:2px;flex-wrap:wrap}
.tab{border:0;background:none;font-family:inherit;font-size:14.9px;font-weight:700;
  color:var(--ink-3);padding:9px 15px;border-radius:11px;cursor:pointer;white-space:nowrap;
  display:flex;align-items:center;gap:8px}
.tab:hover{color:var(--ink)}
.tab i{font-family:var(--mono);font-size:11.5px;font-weight:700;font-style:normal;
  color:var(--mute-2);background:#E5EAE6;border-radius:20px;padding:2px 6px}
.tab.on{background:var(--panel);color:var(--ink);box-shadow:0 1px 3px rgba(21,32,26,.10)}
.tab.on i{background:var(--pos-tint);color:var(--brand)}

.ctl{margin-left:auto;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.find{display:flex;align-items:center;gap:9px;background:#F5F7F5;border:1px solid #E3E8E4;
  border-radius:12px;padding:8px 13px;min-width:210px}
.find:focus-within{border-color:var(--pos);background:var(--panel);
  box-shadow:0 0 0 3px rgba(47,158,99,.12)}
.find svg{width:14px;height:14px;flex:none}
.find input{border:0;background:transparent;outline:0;font-family:inherit;font-size:14.9px;
  color:var(--ink);flex:1;min-width:0}
.find input::placeholder{color:var(--mute-2)}
.sw{display:flex;align-items:center;gap:9px;font-size:14.4px;font-weight:600;color:var(--ink-2);
  cursor:pointer;user-select:none;background:none;border:0;font-family:inherit}
.sw i{width:34px;height:19px;border-radius:20px;background:#DCE2DD;position:relative;flex:none;
  transition:background .16s ease}
.sw i::after{content:"";position:absolute;top:2.5px;left:2.5px;width:14px;height:14px;
  border-radius:50%;background:var(--panel);box-shadow:0 1px 2px rgba(21,32,26,.2);
  transition:transform .16s ease}
.sw.on i{background:var(--brand)}
.sw.on i::after{transform:translateX(15px)}

.jump{display:flex;align-items:center;gap:7px;flex-wrap:wrap;padding:11px 18px;
  border-bottom:1px solid var(--line);background:#FBFCFB}
.jump-l{font-family:var(--mono);font-size:10.9px;font-weight:700;letter-spacing:1.1px;
  text-transform:uppercase;color:var(--mute-2);margin-right:2px}
.jchip{font-family:inherit;font-size:13.8px;font-weight:600;color:var(--ink-3);background:var(--panel);
  border:1px solid #E3E8E4;border-radius:20px;padding:6px 12px;cursor:pointer}
.jchip:hover{border-color:var(--pos);color:var(--ink)}

.twrap{overflow:auto;max-height:none}
table.st{width:100%;border-collapse:separate;border-spacing:0;min-width:1180px}
table.st th{font-family:var(--mono);font-size:11.5px;font-weight:700;letter-spacing:1px;
  text-transform:uppercase;color:var(--mute);text-align:right;padding:12px 12px;
  background:#FBFCFB;border-bottom:1px solid var(--line);white-space:nowrap;
  position:sticky;top:0;z-index:3}
table.st th.item{text-align:left;left:0;z-index:5;min-width:250px;color:var(--ink-2)}
table.st th.last{color:var(--ink);background:#F3F7F4}
table.st th.trend,table.st th.sum{color:var(--mute-2)}

table.st td{padding:0 12px;border-bottom:1px solid var(--line-2);white-space:nowrap;
  text-align:right;background:var(--panel)}
table.st td.item{text-align:left;position:sticky;left:0;z-index:2;min-width:250px;
  border-right:1px solid var(--line-2)}
.cellwrap{padding:9px 0;line-height:1.25}
.v{font-family:var(--mono);font-size:14.9px;font-weight:600;color:var(--ink-2)}
.d{font-family:var(--mono);font-size:12.1px;font-weight:600;padding-top:3px}
.d.up{color:var(--pos)}
.d.dn{color:var(--neg)}
.d.nil{color:#C3CAC6}
.hide-d .d{display:none}
.lbl{font-size:15.5px;font-weight:600;color:var(--ink-2);padding:9px 0}

tr.grp td{background:#F4F7F5;padding:0;position:sticky;left:0}
tr.grp .glabel{font-family:var(--mono);font-size:10.9px;font-weight:800;letter-spacing:1.3px;
  text-transform:uppercase;color:var(--ink-3);padding:9px 12px;display:block}
tr.sub td{background:#F8FAF9;border-top:1px solid #E9EEEA}
tr.sub .lbl{font-weight:800;color:var(--ink)}
tr.sub .v{color:var(--ink);font-weight:700}
tr.sub td.last{background:#F1F6F3}
tr.tot td{background:#F4F7F5;border-top:1px solid #DCE4DE;border-bottom:1px solid #DCE4DE}
tr.tot .lbl{font-weight:800;color:var(--ink)}
tr.tot .v{color:var(--ink);font-weight:800}
tr.r:hover td{background:#F2F7F4}
tr.r.pin td{background:#FCF6E4}
tr.r.pin .lbl{color:var(--ink);font-weight:700}
tr.r{cursor:pointer}
td.last{background:#F8FBF9}
tr.r:hover td.last{background:#EDF5F0}
tr.r.pin td.last{background:#F8F0D8}
td.derived .lbl::after{content:"calc";font-family:var(--mono);font-size:9.8px;font-weight:700;
  letter-spacing:.7px;text-transform:uppercase;color:var(--warn-deep);background:var(--warn-tint);
  border-radius:4px;padding:2px 5px;margin-left:8px;vertical-align:1px}
td.spark{padding:6px 12px}
td.sum{font-family:var(--mono);font-size:13.8px;font-weight:700;color:var(--ink-3)}
td.sum.pos{color:var(--pos-deep)}
td.sum.neg{color:var(--neg)}
.dim{color:#C3CAC6}
.empty{padding:40px 20px;text-align:center;color:var(--mute-2);font-size:15.5px}

.dfoot{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:13px 18px;
  background:#FBFCFB;border-top:1px solid var(--line);font-size:13.2px;color:var(--mute-2)}
.dfoot b{color:var(--ink-3);font-weight:700}
.dfoot .r{margin-left:auto}
.note{display:flex;align-items:flex-start;gap:10px;background:var(--warn-tint);
  border:1px solid #F0DDBB;border-radius:14px;padding:12px 15px;font-size:14.4px;line-height:1.6;
  color:#5C4A22}
.note b{color:#3E3116;font-weight:700}
.note .chip{font-family:var(--mono);font-size:10.3px;font-weight:800;letter-spacing:.8px;
  text-transform:uppercase;color:var(--warn-deep);background:#F7E9CE;border-radius:20px;
  padding:4px 9px;flex:none;margin-top:1px}
.foot{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:0 8px}
.foot-l{font-family:var(--mono);font-size:10.9px;font-weight:700;letter-spacing:1.2px;
  text-transform:uppercase;color:var(--mute)}
.foot-v{font-size:13.8px;color:var(--mute-2)}
.foot-r{margin-left:auto;font-size:13.2px;color:var(--mute-2)}

@media (max-width:820px){
  body{padding:6px 10px 12px}
  #shell{padding:12px;border-radius:18px}
  .ctl{margin-left:0;width:100%}
  .find{flex:1}
}
"""

# --------------------------------------------------------------------------
# Render + interaction — reference logic, reading the injected payload.
# --------------------------------------------------------------------------
STMT_JS = r"""
var D = window.__FC_STMT__ || {years:[], sheets:{}};
var YEARS = D.years || [];
var SHEETS = D.sheets || {};
var TAB_LABELS = {is:"Income statement", bs:"Balance sheet",
                  ra:"Ratio analysis", cs:"Common size"};

function fmtMoney(v){
  if (v === null || v === undefined) return null;
  var a = Math.abs(v);
  if (a >= 1000) return Math.round(v).toLocaleString("en-US");
  return v.toFixed(1);
}
function fmtRatio(v, u){
  if (v === null || v === undefined) return null;
  if (u === "%") return v.toFixed(1) + "%";
  if (u === "x") return v.toFixed(2) + "x";
  if (u === "d") return v.toFixed(1) + "d";
  return v.toFixed(2);
}
function fmtVal(v, row, money){
  var s = money ? fmtMoney(v) : fmtRatio(v, row.u);
  return s === null ? '<span class="dim">—</span>' : s;
}
function delta(row, i, money){
  var v = row.v, cur = v[i], prev = i > 0 ? v[i-1] : null;
  if (cur === null || cur === undefined || prev === null || prev === undefined)
    return {t:"—", c:"nil"};
  if (money){
    if (prev === 0) return {t:"—", c:"nil"};
    var p = (cur - prev) / Math.abs(prev) * 100;
    return {t:(p>=0?"+":"−") + Math.abs(p).toFixed(1) + "%", c:p>=0?"up":"dn"};
  }
  var dd = cur - prev,
      unit = row.u === "%" ? " pp" : (row.u === "d" ? " d" : (row.u === "x" ? "x" : ""));
  return {t:(dd>=0?"+":"−") + Math.abs(dd).toFixed(row.u==="%"||row.u==="d"?1:2) + unit,
          c: Math.abs(dd) < 1e-9 ? "nil" : (dd>0?"up":"dn")};
}
function summary(row, money){
  var v = row.v, idx = [];
  for (var i=0;i<v.length;i++) if (v[i] !== null && v[i] !== undefined) idx.push(i);
  if (!idx.length) return {t:"—", c:""};
  if (money){
    var fi = idx[0], li = idx[idx.length-1], a = v[fi], b = v[li];
    if (a === null || b === null || a <= 0 || b <= 0 || li === fi) return {t:"—", c:""};
    var g = (Math.pow(b/a, 1/(li - fi)) - 1) * 100;
    return {t:(g>=0?"+":"−") + Math.abs(g).toFixed(1) + "%", c:g>=0?"pos":"neg"};
  }
  var s = 0; idx.forEach(function(i){ s += v[i]; });
  return {t:fmtRatio(s/idx.length, row.u), c:""};
}
function spark(row){
  var v = row.v, pts = [], W = 96, H = 24, P = 3;
  var vals = v.filter(function(x){ return x !== null && x !== undefined; });
  if (vals.length < 2) return "";
  var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  var rng = (hi - lo) || 1, n = v.length, first = null, last = null;
  for (var i = 0; i < n; i++){
    if (v[i] === null || v[i] === undefined) continue;
    var x = P + (W - 2*P) * (i / (n - 1));
    var y = H - P - (H - 2*P) * ((v[i] - lo) / rng);
    pts.push([x, y]);
    if (first === null) first = [x, y];
    last = [x, y];
  }
  var d = pts.map(function(p,i){ return (i?"L":"M") + p[0].toFixed(1) + " " + p[1].toFixed(1); }).join(" ");
  var zero = "";
  if (lo < 0 && hi > 0){
    var zy = (H - P - (H - 2*P) * ((0 - lo) / rng)).toFixed(1);
    zero = '<line x1="'+P+'" y1="'+zy+'" x2="'+(W-P)+'" y2="'+zy+'" stroke="#DFE5E0" stroke-width="1"/>';
  }
  return '<svg viewBox="0 0 '+W+' '+H+'" width="'+W+'" height="'+H+'" aria-hidden="true">'
    + zero
    + '<path d="'+d+'" fill="none" stroke="#8FA79A" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>'
    + '<circle cx="'+last[0].toFixed(1)+'" cy="'+last[1].toFixed(1)+'" r="2.6" fill="#177245"/>'
    + '</svg>';
}
function esc(s){ return String(s).replace(/[&<>"]/g, function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]; }); }

var tab = "is", query = "", showD = true, pinned = {};

function render(){
  var S = SHEETS[tab]; if (!S){ return; }
  var money = S.money, nCols = YEARS.length;

  var h = '<tr><th class="item">' + esc(TAB_LABELS[tab] || "") + '</th>';
  YEARS.forEach(function(y, i){
    h += '<th class="' + (i === nCols-1 ? "last" : "") + '">' + esc(y) + '</th>';
  });
  h += '<th class="trend">Trend</th><th class="sum">' + esc(S.sum || "") + '</th></tr>';
  document.getElementById("sthead").innerHTML = h;

  var q = query.trim().toLowerCase(), out = "", shown = 0, chips = "";
  (S.data || []).forEach(function(grp, gi){
    var rows = (grp.rows || []).filter(function(r){ return !q || r.n.toLowerCase().indexOf(q) > -1; });
    if (!rows.length) return;
    var gid = "g" + tab + gi;
    if (grp.g){
      chips += '<button class="jchip" data-go="'+gid+'">'+esc(grp.g)+'</button>';
      out += '<tr class="grp" id="'+gid+'"><td class="item" colspan="'+(nCols+3)+'">'
           + '<span class="glabel">'+esc(grp.g)+'</span></td></tr>';
    }
    rows.forEach(function(r){
      shown++;
      var key = tab + "|" + r.n;
      var cls = "r " + (r.kind || "") + (pinned[key] ? " pin" : "");
      out += '<tr class="'+cls+'" data-key="'+esc(key)+'">';
      out += '<td class="item' + (r.calc ? " derived" : "") + '"><div class="lbl">'+esc(r.n)+'</div></td>';
      r.v.forEach(function(val, i){
        var dl = (val === null || val === undefined) ? null : delta(r, i, money);
        out += '<td class="' + (i === nCols-1 ? "last" : "") + '"><div class="cellwrap">'
             + '<div class="v">' + fmtVal(val, r, money) + '</div>'
             + '<div class="d ' + (dl ? dl.c : "nil") + '">' + (dl ? dl.t : "&nbsp;") + '</div></div></td>';
      });
      var sm = summary(r, money);
      out += '<td class="spark">' + spark(r) + '</td>';
      out += '<td class="sum ' + sm.c + '">' + sm.t + '</td>';
      out += '</tr>';
    });
  });

  document.getElementById("stbody").innerHTML = out;
  document.getElementById("empty").style.display = shown ? "none" : "block";
  document.getElementById("st").style.display = shown ? "" : "none";

  var j = document.getElementById("jump");
  if ((tab === "ra" || tab === "cs") && shown){
    j.style.display = "flex";
    j.innerHTML = '<span class="jump-l">Jump to</span>' + chips;
    j.querySelectorAll("[data-go]").forEach(function(b){
      b.addEventListener("click", function(){
        var el = document.getElementById(b.dataset.go);
        if (el) el.scrollIntoView({behavior:"smooth", block:"center"});
      });
    });
  } else { j.style.display = "none"; }

  document.getElementById("fnote").innerHTML = esc(S.units || "");
  document.getElementById("unitchip").innerHTML =
    money ? "₹ crore" : (tab === "cs" ? "% of base" : "As reported");
  document.getElementById("fsub").innerHTML = esc(S.sub || "");
  document.getElementById("st").classList.toggle("hide-d", !showD);

  document.querySelectorAll("#stbody tr.r").forEach(function(tr){
    tr.addEventListener("click", function(){
      var k = tr.dataset.key;
      if (pinned[k]) delete pinned[k]; else pinned[k] = 1;
      tr.classList.toggle("pin");
    });
  });
}

document.querySelectorAll("#tabs .tab").forEach(function(b){
  b.addEventListener("click", function(){
    document.querySelectorAll("#tabs .tab").forEach(function(x){ x.classList.remove("on"); });
    b.classList.add("on");
    tab = b.dataset.t;
    render();
  });
});
document.getElementById("q").addEventListener("input", function(){ query = this.value; render(); });
document.getElementById("swd").addEventListener("click", function(){
  showD = !showD;
  this.classList.toggle("on", showD);
  this.setAttribute("aria-pressed", showD ? "true" : "false");
  document.getElementById("st").classList.toggle("hide-d", !showD);
});
render();

/* Deep-link from a Dashboard KPI arrow (or the "All" chip): the dashboard stashes
   the target ratio on the parent window, then switches to this page. We open the
   sheet the ratio actually lives on (Ratio analysis preferred) and scroll to it —
   so clicking P/E lands on P/E wherever it sits, not just the page. Match is
   punctuation-insensitive so "pe ratio" finds "P/E Ratio". */
(function(){
  var target=null; try{ target=window.parent.__fcGotoRatio; }catch(e){}
  if(!target) return;
  try{ window.parent.__fcGotoRatio=null; }catch(e){}
  function nrm(s){ return String(s||"").toLowerCase().replace(/[^a-z0-9]/g,""); }
  function openTab(tk){
    var b=document.querySelector('#tabs .tab[data-t="'+tk+'"]');
    document.querySelectorAll('#tabs .tab').forEach(function(x){x.classList.remove('on');});
    if(b) b.classList.add('on');
    tab=tk; render();
  }
  if(String(target)==="__all__"){ openTab("ra"); return; }   // "All" -> Ratio analysis
  var want=nrm(target), order=["ra","is","bs","cs"], exact=null, fuzzy=null;
  order.forEach(function(tk){ var S=SHEETS[tk]; if(!S) return;
    (S.data||[]).forEach(function(g){ (g.rows||[]).forEach(function(r){
      var n=nrm(r.n);
      if(!exact && n===want) exact={tab:tk,name:r.n};
      if(!fuzzy && n.length>3 && (n.indexOf(want)>-1 || want.indexOf(n)>-1)) fuzzy={tab:tk,name:r.n};
    }); }); });
  var hit=exact||fuzzy; if(!hit) return;
  openTab(hit.tab);
  setTimeout(function(){
    var w=nrm(hit.name), row=null;
    document.querySelectorAll('#stbody tr.r').forEach(function(tr){
      var lbl=(tr.querySelector('.lbl')||{}).textContent||"";
      if(!row && nrm(lbl)===w) row=tr;
    });
    if(!row) return;
    row.scrollIntoView({behavior:'smooth', block:'center'});
    var cells=row.querySelectorAll('td');
    cells.forEach(function(c){ c.style.transition='background .5s'; c.style.background='#FFF6CF'; });
    setTimeout(function(){ cells.forEach(function(c){ c.style.background=''; }); }, 2000);
  }, 160);
})();
"""

# Streamlit frame-fit — preserved verbatim from the reference component.
STMT_FIT = r"""
(function(){
  function fit(){
    try{
      var s=document.getElementById('shell'); if(!s) return;
      var h=Math.ceil(s.getBoundingClientRect().height)+58;
      try{ window.parent.postMessage(
        {isStreamlitMessage:true, type:'streamlit:setFrameHeight', height:h}, '*'); }catch(e){}
      try{
        var fe=window.frameElement;
        if(fe){
          var cur=parseInt(fe.style.height)||0;
          if(Math.abs(cur-h)>1){
            fe.style.setProperty('height', h+'px', 'important');
            fe.setAttribute('height', h);
          }
          var el=fe.parentElement;
          for(var i=0;i<5 && el;i++){
            var tid=el.getAttribute && el.getAttribute('data-testid');
            if(tid==='stElementContainer'||tid==='stVerticalBlock'||tid==='stVerticalBlockBorderWrapper'){
              if(el.style.height!=='auto') el.style.height='auto';
              el.style.minHeight='0px';
            }
            if(tid==='stMain'||tid==='stAppViewContainer') break;
            el=el.parentElement;
          }
        }
      }catch(e){}
    }catch(e){}
  }
  function schedule(){fit();for(var k=1;k<=12;k++)setTimeout(fit,k*250);}
  window.addEventListener('load',schedule); schedule();
  if(window.ResizeObserver){
    var ro=new ResizeObserver(fit);
    var s=document.getElementById('shell'); if(s) ro.observe(s);
    ro.observe(document.documentElement);
    if(document.body) ro.observe(document.body);
  }
  window.addEventListener('resize',fit);
  if(window.visualViewport){
    window.visualViewport.addEventListener('resize',fit);
    window.visualViewport.addEventListener('scroll',fit);
  }
  setInterval(fit, 1000);
})();
"""


def _tabs_html(payload: dict) -> str:
    """Four tab buttons with real per-sheet row counts."""
    labels = [("is", "Income statement"), ("bs", "Balance sheet"),
              ("ra", "Ratio analysis"), ("cs", "Common size")]
    out = []
    for key, label in labels:
        sheet = payload["sheets"].get(key, {})
        count = sum(len(g.get("rows", [])) for g in sheet.get("data", []))
        on = " on" if key == "is" else ""
        out.append(f'<button class="tab{on}" data-t="{key}">{label} <i>{count}</i></button>')
    return "".join(out)


def build(model) -> tuple[str, int]:
    """Return (html, initial_height) for the redesigned Statements component."""
    payload = S.statements_payload(model)
    company = escape((payload.get("company") or "Company").title())
    window = escape(payload.get("window") or "")
    periods = payload.get("periods") or 0
    source = escape(payload.get("source") or "uploaded model")
    data_json = json.dumps(payload, ensure_ascii=False, allow_nan=False)

    body = f"""<div id="shell">
  <div class="phead">
    <div>
      <h1>Statements</h1>
      <div class="sub">Every period as filed, straight from the uploaded model.
        Click any line to pin it while you scan across.</div>
    </div>
    <div class="feed">
      <div class="feed-i"><div class="feed-l">Company</div><div class="feed-v">{company}</div></div>
      <div class="feed-i"><div class="feed-l">Window</div><div class="feed-v">{window}</div></div>
      <div class="feed-i"><div class="feed-l">Units</div><div class="feed-v" id="unitchip">&#8377; crore</div></div>
    </div>
  </div>

  <div class="deck">
    <div class="bar">
      <div class="tabs" id="tabs">{_tabs_html(payload)}</div>
      <div class="ctl">
        <div class="find">
          <svg viewBox="0 0 24 24" fill="none" stroke="#9AA09D" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
          <input id="q" type="text" placeholder="Find a line item&hellip;" aria-label="Find a line item">
        </div>
        <button class="sw on" id="swd" aria-pressed="true"><i></i>Show change</button>
      </div>
    </div>
    <div class="jump" id="jump" style="display:none"><span class="jump-l">Jump to</span></div>
    <div class="twrap">
      <table class="st" id="st"><thead id="sthead"></thead><tbody id="stbody"></tbody></table>
      <div class="empty" id="empty" style="display:none">No line item matches that search.</div>
    </div>
    <div class="dfoot">
      <b id="fnote">Figures in &#8377; crore</b>
      <span id="fsub">&mdash; change shown beneath each figure is versus the prior year.</span>
      <span class="r">{periods} periods &middot; {window} &middot; {source}</span>
    </div>
  </div>

  <div class="foot">
    <span class="foot-l">Source</span>
    <span class="foot-v">Uploaded model only &mdash; no external data on this page.</span>
    <span class="foot-r">FundaCheck Research</span>
  </div>
</div>"""

    html = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:'
        'wght@400;500;600;700;800&display=swap" rel="stylesheet">'
        f"<style>{STMT_CSS}</style></head><body>{body}"
        f"<script>window.__FC_STMT__ = {data_json};</script>"
        f"<script>{STMT_JS}</script>"
        f"<script>{STMT_FIT}</script>"
        "</body></html>"
    )
    return html, 900
