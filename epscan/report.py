"""Genera el reporte HTML (GitHub Pages) y los CSV para Google Sheets."""
from __future__ import annotations

import html
import math
from datetime import datetime
from pathlib import Path

import pandas as pd

# ── formateadores ───────────────────────────────────────────────────────────


def _n(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def pct(x, d=1, sign=True):
    v = _n(x)
    if v is None:
        return "—"
    return f"{v * 100:+.{d}f}%" if sign else f"{v * 100:.{d}f}%"


def pct_raw(x):  # ya viene en % (sorpresa EPS de Yahoo)
    v = _n(x)
    return "—" if v is None else f"{v:+.1f}%"


def mult(x):
    v = _n(x)
    return "—" if v is None else f"{v:.1f}x"


def money(x):
    v = _n(x)
    if v is None:
        return "—"
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{v / div:.1f}{suf}"
    return f"{v:.0f}"


def price(x):
    v = _n(x)
    return "—" if v is None else f"{v:,.2f}"


def num(x, d=2):
    v = _n(x)
    return "—" if v is None else f"{v:.{d}f}"


def yesno(x):
    if x is True or x == "True":
        return '<span class="ok">Sí</span>'
    if x is False or x == "False":
        return '<span class="muted">No</span>'
    return "—"


def plain(x):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else html.escape(str(x))


def ticker_cell(row):
    t = html.escape(str(row["ticker"]))
    name = html.escape(str(row.get("name") or ""))[:34]
    return (f'<div class="tk"><b>{t}</b><span class="lk">'
            f'<a href="https://www.tradingview.com/chart/?symbol={t}" target="_blank" rel="noopener">TV</a>'
            f'<a href="https://finviz.com/quote.ashx?t={t}" target="_blank" rel="noopener">FV</a></span></div>'
            f'<div class="nm">{name}</div>')


def score_cell(v):
    s = _n(v)
    if s is None:
        return "—"
    cls = "hi" if s >= 70 else "md" if s >= 50 else "lo"
    return f'<span class="sc {cls}">{int(s)}</span>'


def status_cell(v):
    cls = {"Sobre MM10": "ok", "Entre MM10 y MM20": "warn", "Bajo MM20": "bad", "Perdió LOD": "bad",
           "Breakout": "ok", "Setup": "warn"}.get(str(v), "")
    return f'<span class="pill {cls}">{plain(v)}</span>'


# ── tablas ──────────────────────────────────────────────────────────────────

def table(rows: list[dict], cols: list[tuple], empty: str) -> str:
    if not rows:
        return f'<p class="empty">{empty}</p>'
    head = "".join(f'<th title="{html.escape(tip)}">{html.escape(h)}</th>' for _, h, _, tip in cols)
    body = []
    for r in rows:
        tds = []
        for key, _, fmt, _ in cols:
            val = r if key == "*" else r.get(key)
            sortv = _n(val) if key != "*" else r.get("ticker")
            sv = "" if sortv is None else html.escape(str(sortv))
            tds.append(f'<td data-v="{sv}">{fmt(val)}</td>')
        body.append("<tr>" + "".join(tds) + "</tr>")
    return (f'<div class="tw"><table class="sortable"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


EP_COLS = [
    ("*", "Ticker", ticker_cell, ""),
    ("score", "Score", score_cell, "Score 0-100 (ver 'Cómo leer')"),
    ("gap", "Gap", pct, "Apertura vs cierre previo"),
    ("chg", "Var.", pct, "Cierre vs cierre previo"),
    ("rvol", "RVol", mult, "Volumen / promedio 50 ruedas previas"),
    ("dollar_vol", "USD op.", money, "Monto operado en el día"),
    ("close_pos", "Cierre rango", lambda v: pct(v, 0, False), "Dónde cerró dentro del rango del día (100% = en el máximo)"),
    ("ret_3m", "Var. 3m previa", pct, "Suba de las 63 ruedas previas al gap: cuanto menor, más 'olvidada'"),
    ("ext_sma50", "vs MM50", pct, "Distancia a la media de 50 el día previo"),
    ("neglected", "Neglect", yesno, "Cumple el filtro de acción olvidada"),
    ("earnings_catalyst", "Resultados", yesno, "Publicó resultados en la ventana previa al gap"),
    ("eps_surprise_pct", "Sorpresa EPS", pct_raw, "Sorpresa vs consenso (Yahoo)"),
    ("rev_yoy_q0", "Ventas YoY", pct, "Crecimiento interanual del último trimestre"),
    ("rev_yoy_q1", "YoY trim. ant.", pct, "Mismo dato del trimestre anterior: si el último es mayor, hay aceleración"),
    ("mkt_cap", "Mkt cap", money, ""),
    ("sector", "Sector", plain, ""),
    ("stop_ref", "Stop (LOD)", price, "Mínimo del día del gap"),
    ("risk_pct", "Riesgo", lambda v: pct(v, 1, False), "Distancia del cierre al LOD"),
]

DELAYED_COLS = [
    ("*", "Ticker", ticker_cell, ""),
    ("kind", "Estado", status_cell, "Breakout: cierre sobre el máximo de la consolidación. Setup: consolidación ajustada cerca de máximos"),
    ("score", "Score EP", score_cell, "Score del EP original"),
    ("ep_date", "Fecha EP", plain, ""),
    ("days", "Ruedas", lambda v: plain(int(v)) if _n(v) is not None else "—", "Ruedas desde el EP"),
    ("ep_gap", "Gap EP", pct, ""),
    ("ep_rvol", "RVol EP", mult, ""),
    ("ret_since_ep", "Desde EP", pct, "Cierre actual vs cierre del día del EP"),
    ("from_high", "vs máx.", pct, "Distancia al máximo desde el EP"),
    ("tight_range", "Rango 5d", lambda v: pct(v, 1, False), "Rango de las últimas ruedas / precio"),
    ("vol_dryup", "Vol / vol EP", lambda v: pct(v, 0, False), "Volumen promedio reciente vs volumen del día del EP"),
    ("held_lod", "Sostiene LOD", yesno, "Nunca perforó el mínimo del día del EP"),
    ("rvol", "RVol hoy", mult, ""),
    ("earnings_catalyst", "Resultados", yesno, ""),
    ("rev_yoy_q0", "Ventas YoY", pct, ""),
    ("stop_ref", "Stop", price, "Mínimo de la consolidación"),
    ("risk_pct", "Riesgo", lambda v: pct(v, 1, False), ""),
]

FOLLOW_COLS = [
    ("*", "Ticker", ticker_cell, ""),
    ("status", "Estado", status_cell, ""),
    ("ep_date", "Fecha EP", plain, ""),
    ("days", "Ruedas", lambda v: plain(int(v)) if _n(v) is not None else "—", ""),
    ("ep_gap", "Gap EP", pct, ""),
    ("ret_since", "Desde EP", pct, "Cierre actual vs cierre del día del EP"),
    ("max_gain", "Máx. ganancia", pct, "Máximo alcanzado vs cierre del día del EP"),
    ("close", "Cierre", price, ""),
    ("ep_lod", "LOD EP", price, ""),
    ("gap_filled", "Gap cubierto", yesno, "Algún mínimo posterior tocó el cierre previo al gap"),
]


def stats_table(df: pd.DataFrame, min_n: int, index_label: str) -> str:
    if df is None or df.empty:
        return '<p class="empty">Todavía no hay señales cerradas. Las estadísticas aparecen a medida que las señales completan su horizonte o tocan el stop.</p>'
    cols = [("n", "N", lambda v: plain(int(v))), ("win_rate", "Aciertos", lambda v: pct(v, 0, False)),
            ("avg_r", "R prom.", lambda v: num(v)), ("median_r", "R mediana", lambda v: num(v)),
            ("avg_win_r", "R ganador", lambda v: num(v)), ("avg_loss_r", "R perdedor", lambda v: num(v)),
            ("profit_factor", "Profit factor", lambda v: num(v)), ("best_r", "Mejor R", lambda v: num(v))]
    cols += [(c, c.replace("avg_ret_", "Ret. ") + "d", pct) for c in df.columns if c.startswith("avg_ret_")]
    head = f"<th>{index_label}</th>" + "".join(f"<th>{h}</th>" for _, h, _ in cols)
    rows = []
    for idx, r in df.iterrows():
        label = " · ".join(map(str, idx)) if isinstance(idx, tuple) else str(idx)
        warn = ' <span class="warn-s" title="Muestra chica: no concluir">muestra chica</span>' \
            if _n(r["n"]) is not None and r["n"] < min_n else ""
        rows.append(f"<tr><td><b>{html.escape(TYPE_LABEL.get(label, label))}</b>{warn}</td>"
                    + "".join(f'<td data-v="{_n(r[k]) if _n(r[k]) is not None else ""}">{f(r[k])}</td>' for k, _, f in cols)
                    + "</tr>")
    return (f'<div class="tw"><table class="sortable"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


TYPE_LABEL = {"EP": "EP del día", "9M": "9M EP", "DEP": "Delayed EP (breakout)", "TODAS": "Todas"}


# ── página ──────────────────────────────────────────────────────────────────

def render(ctx: dict) -> str:
    reg = ctx["regime"]
    reg_cls = {"Favorable": "ok", "Neutral": "warn", "Desfavorable": "bad"}.get(reg["label"], "")
    chips = "".join(
        f'<div class="ix"><b>{i["symbol"]}</b> <span>{price(i["close"])}</span> '
        f'<span class="{"up" if i["chg"] >= 0 else "dn"}">{pct(i["chg"])}</span>'
        f'<small>MM20 {"✓" if i["above_sma20"] else "✗"} · MM50 {"✓" if i["above_sma50"] else "✗"} · 1m {pct(i["ret_1m"], 1)}</small></div>'
        for i in reg["indices"])
    ep, nm, dl, fu = ctx["ep"], ctx["nine_m"], ctx["delayed"], ctx["followup"]
    brk = [r for r in dl if r["kind"] == "Breakout"]
    setups = [r for r in dl if r["kind"] == "Setup"]
    kpis = [("EPs del día", len(ep), "ep"), ("9M EPs", len(nm), "nm"),
            ("Delayed · breakout", len(brk), "dl"), ("Delayed · setup", len(setups), "dl"),
            ("En seguimiento", len(fu), "fu")]
    kpi_html = "".join(f'<a class="kpi" href="#{a}"><span>{v}</span><small>{k}</small></a>' for k, v, a in kpis)
    tr = ctx["tracking"]
    cfg = ctx["cfg"]

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scanner EP · {ctx['asof']}</title>
<style>{CSS}</style></head>
<body>
<header>
  <div class="wrap">
    <div class="eyebrow">Scanner de Episodic Pivots</div>
    <h1>Al cierre del {ctx['asof_long']}</h1>
    <div class="meta">Universo: {ctx['universe_n']:,} acciones US · Datos: Yahoo Finance · Generado {ctx['generated']}</div>
  </div>
</header>
<main class="wrap">
  <section class="regime {reg_cls}">
    <div><small>Contexto de mercado</small><strong>{reg['label']}</strong>
    <p>Los EPs rinden mucho mejor con índices en tendencia. En contexto desfavorable, más gaps se devuelven en el día.</p></div>
    <div class="ixs">{chips}</div>
  </section>
  <nav class="kpis">{kpi_html}</nav>

  <section id="ep"><h2>EPs del día</h2>
  <p class="sub">Gap ≥ {pct(cfg.EP['min_gap'], 0, False)}, RVol ≥ {cfg.EP['min_rvol']:.0f}x, operado ≥ {money(cfg.EP['min_dollar_vol'])} USD{', sin devolver el gap entero' if cfg.EP['require_hold'] else ''}. Ordenados por score.</p>
  {table(ep, EP_COLS, 'Sin EPs que cumplan los filtros hoy.')}</section>

  <section id="nm"><h2>9 Million EPs</h2>
  <p class="sub">Volumen ≥ {cfg.NINE_M['min_volume'] / 1e6:.0f}M acciones, suba ≥ {pct(cfg.NINE_M['min_change'], 0, False)} y RVol ≥ {cfg.NINE_M['min_rvol']:.0f}x, que no califican como EP clásico.</p>
  {table(nm, EP_COLS, 'Sin 9M EPs hoy.')}</section>

  <section id="dl"><h2>Delayed EPs</h2>
  <p class="sub">EPs de hace {cfg.DELAYED['min_days']} a {cfg.DELAYED['max_days']} ruedas que nunca cerraron debajo del cierre previo al gap. <b>Breakout</b>: hoy cerró sobre el máximo de la consolidación. <b>Setup</b>: consolidación ajustada a menos de {pct(cfg.DELAYED['max_from_high'], 0, False)} del máximo, para vigilar.</p>
  <h3>Breakouts</h3>{table(brk, DELAYED_COLS, 'Sin breakouts de delayed EP hoy.')}
  <h3>Setups en formación</h3>{table(setups, DELAYED_COLS, 'Sin setups en formación.')}</section>

  <section id="fu"><h2>Seguimiento de EPs recientes</h2>
  <p class="sub">Todos los EPs de las últimas {cfg.FOLLOWUP['days']} ruedas y cómo vienen. Útil para gestionar posiciones abiertas.</p>
  {table(fu, FOLLOW_COLS, 'Sin EPs en las últimas ruedas.')}</section>

  <section id="st"><h2>Resultados históricos del scanner</h2>
  <p class="sub">Señales registradas: {tr['total']} · cerradas: {tr['closed']} · abiertas: {tr['open']} · pendientes de entrada: {tr['pending']} · invalidadas: {tr['invalid']}.
  Simulación: entrada en la apertura siguiente, stop en el LOD (o mínimo de la consolidación en delayed EPs), salida por stop o a las {cfg.TRACKING['horizon']} ruedas. Sin comisiones ni slippage.</p>
  <h3>Por tipo de señal</h3>{stats_table(ctx['stats_type'], cfg.TRACKING['min_sample_warning'], 'Tipo')}
  <h3>Por tipo y contexto de mercado al momento de la señal</h3>{stats_table(ctx['stats_regime'], cfg.TRACKING['min_sample_warning'], 'Tipo · contexto')}
  <p class="note">Un scanner diario no ve la apertura: el trade real de día 1 (compra en el ORH, stop en el LOD) va a tener otro resultado que el simulado acá. Esta tabla sirve para comparar tipos de señal y contextos entre sí, no como estimación exacta del rendimiento.</p>
  </section>

  <details class="how"><summary>Cómo leer el score</summary>
  <p>Ordena las señales; no es una señal en sí misma. Tamaño del gap (20), volumen relativo (20), neglect: cuánto subió en los 3 meses previos (20), cierre dentro del rango del día (15) y catalizador/números: resultados en la fecha, sorpresa de EPS y crecimiento de ventas (25).</p>
  <p>Los fundamentals vienen de Yahoo y a veces faltan o llegan con demora. Si el score es alto sin datos de resultados, revisar el catalizador a mano antes de operar.</p>
  </details>
  <footer>Descargas: <a href="data/ep_today.csv">EPs del día</a> · <a href="data/nine_m.csv">9M</a> · <a href="data/delayed.csv">Delayed</a> · <a href="data/followup.csv">Seguimiento</a> · <a href="data/signals.csv">Registro de señales</a> · <a href="data/stats.csv">Estadísticas</a></footer>
</main>
<script>{JS}</script>
</body></html>"""


CSS = """
:root{--bg:#f5f7fa;--card:#fff;--ink:#1d2430;--mut:#667085;--line:#e3e7ee;--head:#10376b;--acc:#2f6fd0;
--ok:#1f7a4d;--okb:#e5f4ec;--warn:#9a6a00;--warnb:#fbf1d9;--bad:#b3261e;--badb:#fbe7e5}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f141b;--card:#161d27;--ink:#e6eaf0;--mut:#8b96a8;--line:#263142;
--head:#0c2140;--acc:#6ea0ec;--ok:#5cc58d;--okb:#12301f;--warn:#e0b44c;--warnb:#33290f;--bad:#f07b72;--badb:#3a1714}}
:root[data-theme="dark"]{--bg:#0f141b;--card:#161d27;--ink:#e6eaf0;--mut:#8b96a8;--line:#263142;--head:#0c2140;--acc:#6ea0ec;
--ok:#5cc58d;--okb:#12301f;--warn:#e0b44c;--warnb:#33290f;--bad:#f07b72;--badb:#3a1714}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 "Segoe UI",system-ui,-apple-system,Roboto,Arial,sans-serif}
.wrap{max-width:1400px;margin:0 auto;padding:0 16px}
header{background:linear-gradient(100deg,#10376b,#1b4f94);color:#fff;padding:22px 0 20px}
.eyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;opacity:.75}
h1{margin:4px 0 2px;font-size:24px;font-weight:600}.meta{font-size:12px;opacity:.75}
h2{font-size:18px;margin:0 0 4px;color:var(--ink)}h3{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:18px 0 6px}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;margin:14px 0}
.sub{margin:0 0 10px;color:var(--mut);font-size:13px}
.regime{display:flex;gap:20px;flex-wrap:wrap;align-items:center;border-left:4px solid var(--mut)}
.regime.ok{border-left-color:var(--ok)}.regime.warn{border-left-color:var(--warn)}.regime.bad{border-left-color:var(--bad)}
.regime small{display:block;color:var(--mut);text-transform:uppercase;font-size:11px;letter-spacing:.08em}
.regime strong{font-size:22px}.regime.ok strong{color:var(--ok)}.regime.warn strong{color:var(--warn)}.regime.bad strong{color:var(--bad)}
.regime p{margin:4px 0 0;color:var(--mut);font-size:12px;max-width:380px}
.ixs{display:flex;gap:10px;flex-wrap:wrap}.ix{border:1px solid var(--line);border-radius:6px;padding:8px 12px;min-width:170px}
.ix small{display:block;color:var(--mut);font-size:11px;margin-top:2px}
.up{color:var(--ok)}.dn{color:var(--bad)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-top:3px solid var(--acc);border-radius:8px;padding:10px 14px;text-decoration:none;color:var(--ink)}
.kpi span{font-size:26px;font-weight:600;display:block}.kpi small{color:var(--mut)}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th{position:sticky;top:0;background:var(--head);color:#fff;font-weight:600;text-align:right;padding:7px 9px;white-space:nowrap;cursor:pointer;user-select:none;font-size:12px}
th:first-child,td:first-child{text-align:left}th.asc:after{content:" ▲"}th.desc:after{content:" ▼"}
td{padding:6px 9px;border-top:1px solid var(--line);text-align:right;white-space:nowrap}
tbody tr:hover{background:color-mix(in srgb,var(--acc) 7%,transparent)}
.tk{display:flex;gap:8px;align-items:center}.tk b{font-size:14px}.lk a{font-size:10px;color:var(--acc);text-decoration:none;border:1px solid var(--line);border-radius:3px;padding:0 4px;margin-right:3px}
.nm{color:var(--mut);font-size:11px;max-width:220px;overflow:hidden;text-overflow:ellipsis}
.sc{display:inline-block;min-width:32px;text-align:center;border-radius:4px;padding:1px 6px;font-weight:600}
.sc.hi{background:var(--okb);color:var(--ok)}.sc.md{background:var(--warnb);color:var(--warn)}.sc.lo{background:var(--line);color:var(--mut)}
.pill{border-radius:10px;padding:1px 8px;font-size:12px;background:var(--line)}
.pill.ok{background:var(--okb);color:var(--ok)}.pill.warn{background:var(--warnb);color:var(--warn)}.pill.bad{background:var(--badb);color:var(--bad)}
.ok{color:var(--ok)}.muted{color:var(--mut)}.empty{color:var(--mut);font-style:italic;margin:6px 0}
.warn-s{font-size:10px;color:var(--warn);border:1px solid var(--warn);border-radius:3px;padding:0 4px;margin-left:6px}
.note{font-size:12px;color:var(--mut);border-left:3px solid var(--line);padding-left:10px}
.how{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;color:var(--mut)}
.how summary{cursor:pointer;color:var(--ink);font-weight:600}
footer{padding:18px 0 40px;color:var(--mut);font-size:12px}footer a{color:var(--acc)}
@media (max-width:640px){h1{font-size:19px}section{padding:12px}}
"""

JS = """
document.querySelectorAll('table.sortable').forEach(t=>{
  t.querySelectorAll('th').forEach((th,i)=>th.addEventListener('click',()=>{
    const tb=t.tBodies[0],rows=[...tb.rows],asc=!th.classList.contains('desc')&&th.classList.contains('asc')?false:!th.classList.contains('asc');
    t.querySelectorAll('th').forEach(h=>h.classList.remove('asc','desc'));th.classList.add(asc?'asc':'desc');
    rows.sort((a,b)=>{const x=a.cells[i].dataset.v,y=b.cells[i].dataset.v,nx=parseFloat(x),ny=parseFloat(y);
      if(x===''&&y!=='')return 1;if(y===''&&x!=='')return -1;
      const c=(!isNaN(nx)&&!isNaN(ny))?nx-ny:String(x).localeCompare(String(y));return asc?c:-c});
    rows.forEach(r=>tb.appendChild(r));}));});
"""


MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def write(out_dir: Path, ctx: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "data").mkdir(exist_ok=True)
    d = pd.Timestamp(ctx["asof"])
    ctx["asof_long"] = f"{DIAS[d.dayofweek]} {d.day} de {MESES[d.month - 1]} de {d.year}"
    ctx["generated"] = datetime.now().strftime("%d/%m/%Y %H:%M")
    (out_dir / "index.html").write_text(render(ctx), encoding="utf-8")
    for key, fname in (("ep", "ep_today"), ("nine_m", "nine_m"), ("delayed", "delayed"), ("followup", "followup")):
        pd.DataFrame(ctx[key]).to_csv(out_dir / "data" / f"{fname}.csv", index=False)
    ctx["signals"].to_csv(out_dir / "data" / "signals.csv", index=False)
    st = ctx["stats_type"]
    (st if st is not None and not st.empty else pd.DataFrame()).to_csv(out_dir / "data" / "stats.csv")
