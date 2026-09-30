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

def stop_adr_cell(v):
    x = _n(v)
    if x is None:
        return "—"
    cls = "ok" if x <= 1.0 else "warn-t" if x <= 1.5 else "bad-t"
    return f'<span class="{cls}">{x:.1f}</span>'


def calc_cell(r):
    e, st = _n(r.get("plan_entry")), _n(r.get("plan_stop"))
    if e is None or st is None or e <= st:
        return "—"
    return f'<span class="calc" data-e="{e:.4f}" data-s="{st:.4f}">—</span>'


PLAN_COLS = [
    ("adr", "ADR", lambda v: pct(v, 1, False), "Rango diario promedio de las 20 ruedas previas"),
    ("plan_entry", "Entrada ref.", price,
     "EP y breakout: cierre de hoy; la entrada real es el ORH de mañana. Setup: máximo de la consolidación (orden stop)"),
    ("plan_natural_stop", "Stop natural", price, "LOD del día (EP) o mínimo de la consolidación (delayed)"),
    ("stop_adr", "Stop/ADR", stop_adr_cell, "Ancho del stop natural medido en ADRs. Qullamaggie: ≤ 1, como máximo 1,5"),
    ("plan_stop", "Stop plan", price, "Stop natural o, si es más ancho que 1 ADR, entrada − 1 ADR"),
    ("plan_stop_pct", "Riesgo", lambda v: pct(v, 1, False), "Distancia entre la entrada de referencia y el stop del plan"),
    ("*", "Tamaño", calc_cell, "Acciones y monto según la calculadora de arriba"),
]


def h_is_score(header: str) -> bool:
    return header.startswith("Score")


def table(rows: list[dict], cols: list[tuple], empty: str) -> str:
    if not rows:
        return f'<p class="empty">{empty}</p>'
    head = "".join(f'<th title="{html.escape(tip)}">{html.escape(h)}</th>' for _, h, _, tip in cols)
    body = []
    for r in rows:
        tds = []
        for key, hdr, fmt, _tip in cols:
            val = r if key == "*" else r.get(key)
            if key == "*":
                sortv = r.get("score") if h_is_score(hdr) else r.get("ticker")
            elif _n(val) is not None:
                sortv = _n(val)
            elif val is None or isinstance(val, float):
                sortv = None
            else:
                sortv = str(val)
            sv = "" if sortv is None else html.escape(str(sortv))
            tds.append(f'<td data-v="{sv}">{fmt(val)}</td>')
        body.append("<tr>" + "".join(tds) + "</tr>")
    return (f'<div class="tw"><table class="sortable"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


EP_COLS = [
    ("*", "Ticker", ticker_cell, ""),
    ("*", "Score", lambda r: score_cell(r.get("score")) + (
        ' <span class="warn-s" title="Yahoo no devolvió fundamentals: revisar el catalizador a mano">sin datos</span>'
        if r.get("fund_missing") else ""), "Score 0-100 (ver 'Cómo leer')"),
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
] + PLAN_COLS

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
] + PLAN_COLS

REJECTED_COLS = [
    ("*", "Ticker", ticker_cell, ""),
    ("kind", "Tipo", plain, ""),
    ("reasons", "Motivo del descarte", plain, ""),
    ("gap", "Gap", pct, ""),
    ("chg", "Var.", pct, ""),
    ("rvol", "RVol", mult, ""),
    ("dollar_vol", "USD op.", money, ""),
    ("close", "Cierre", price, ""),
    ("close_pos", "Cierre rango", lambda v: pct(v, 0, False), ""),
    ("off_52wh", "vs máx. 52s", pct, "Cierre previo vs máximo de 52 semanas"),
    ("ret_3m", "Var. 3m previa", pct, ""),
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
    ("sma10", "MM10", price, ""),
    ("sma20", "MM20", price, ""),
    ("action", "Acción sugerida", plain, "Reglas de gestión contando ruedas desde el día del EP"),
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
            ("En seguimiento", len(fu), "fu"), ("Descartados", len(ctx["rejected"]), "rj")]
    kpi_html = "".join(f'<a class="kpi" href="#{a}"><span>{v}</span><small>{k}</small></a>' for k, v, a in kpis)
    tr = ctx["tracking"]
    tc = ctx["cfg"].TRADE
    cfg = ctx["cfg"]

    q_ok = reg.get("qqq_ema_ok")
    QQQ = ("" if q_ok is None else
           f'<p class="qqq {"ok" if q_ok else "bad-t"}">QQQ: EMA10 {"sobre" if q_ok else "debajo de"} EMA20 · '
           f'{"luz verde para operar" if q_ok else "reducir tamaño o esperar"}</p>')
    PROV = ('<section class="prov"><strong>Reporte provisional</strong> Se generó con el mercado abierto: '
            'la barra del día todavía no cerró, así que gaps, volumen y cierres pueden cambiar. '
            'No se registraron señales. El reporte definitivo sale con la corrida automática posterior al cierre.</section>'
            if ctx.get("provisional") else "")
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scanner EP · {ctx['asof']}{' (provisional)' if ctx.get('provisional') else ''}</title>
<style>{CSS}</style></head>
<body>
<header>
  <div class="wrap">
    <div class="eyebrow">Scanner de Episodic Pivots</div>
    <h1>{'Intradiario (provisional) del' if ctx.get('provisional') else 'Al cierre del'} {ctx['asof_long']}</h1>
    <div class="meta">Universo: {ctx['universe_n']:,} acciones US · Datos: Yahoo Finance · Generado {ctx['generated']}</div>
  </div>
</header>
<main class="wrap">
  {PROV}
  <section class="regime {reg_cls}">
    <div><small>Contexto de mercado</small><strong>{reg['label']}</strong>
    <p>Los EPs rinden mucho mejor con índices en tendencia. En contexto desfavorable, más gaps se devuelven en el día.</p>
    {QQQ}</div>
    <div class="ixs">{chips}</div>
  </section>
  <nav class="kpis">{kpi_html}</nav>
  <section class="calcbar">
    <strong>Tamaño de posición</strong>
    <label>Capital USD <input id="cap" type="number" min="0" step="1000" placeholder="100000"></label>
    <label>Riesgo por trade % <input id="rk" type="number" min="0" step="0.05" value="{tc['default_risk_pct']}"></label>
    <label>Máx. por posición % <input id="mx" type="number" min="0" step="1" value="{tc['default_max_position_pct']}"></label>
    <small>Se guarda sólo en este navegador; no se sube a ningún lado. Qullamaggie: riesgo 0,25–1% por trade, posiciones de 10–20% (nunca más de 25–30%).</small>
  </section>

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

  <section id="rj"><h2>Descartados por los filtros de calidad</h2>
  <p class="sub">Cumplieron gap/volumen pero no los filtros de calidad (precio ≥ {cfg.QUALITY['min_price']:.0f}, cierre en la mitad superior del rango, no más de {pct(-cfg.QUALITY['max_off_52wh'], 0, False)} abajo del máximo de 52 semanas, sin pump de más de {pct(cfg.QUALITY['max_prior_runup'], 0, False)} en las {cfg.QUALITY['runup_window']} ruedas previas). Se listan para auditar los filtros, no para operar.</p>
  {table(ctx['rejected'], REJECTED_COLS, 'Ningún descarte hoy.')}</section>

  <section id="fu"><h2>Gestión de EPs recientes</h2>
  <p class="sub">EPs de las últimas {cfg.FOLLOWUP['days']} ruedas con la acción que marcan las reglas: parcial de 1/3–1/2 entre los días {tc['partial_days'][0]} y {tc['partial_days'][1]} si está en ganancia (con stop a breakeven), y después salida en el primer cierre bajo la MM{tc['trail_ma']}. Las ruedas se cuentan desde el día del EP: si entraste un día después, corré la cuenta.</p>
  {table(fu, FOLLOW_COLS, 'Sin EPs en las últimas ruedas.')}</section>

  <section id="st"><h2>Resultados históricos del scanner</h2>
  <p class="sub">Señales registradas: {tr['total']} · cerradas: {tr['closed']} · abiertas: {tr['open']} · pendientes de entrada: {tr['pending']} · invalidadas: {tr['invalid']}.
  Simulación con las reglas de Qullamaggie: entrada en la apertura siguiente; stop en el LOD (o mínimo de la consolidación) con tope de {tc['max_stop_adr']:g} ADR; venta de {tc['partial_fraction'] * 100:.0f}% entre los días {tc['partial_days'][0]} y {tc['partial_days'][1]} si está en ganancia, con stop a breakeven; el resto sale en el primer cierre bajo la MM{tc['trail_ma']}. Sin comisiones ni slippage.</p>
  <h3>Por tipo de señal</h3>{stats_table(ctx['stats_type'], cfg.TRACKING['min_sample_warning'], 'Tipo')}
  <h3>Por tipo y contexto de mercado al momento de la señal</h3>{stats_table(ctx['stats_regime'], cfg.TRACKING['min_sample_warning'], 'Tipo · contexto')}
  <p class="note">Un scanner diario no ve la apertura: el trade real de día 1 (compra en el ORH, stop en el LOD) va a tener otro resultado que el simulado acá. Esta tabla sirve para comparar tipos de señal y contextos entre sí, no como estimación exacta del rendimiento.</p>
  </section>

  <details class="how"><summary>Reglas de ejecución (Qullamaggie)</summary>
  <p><b>Qué operar.</b> Gap de 10% o más con volumen enorme: idealmente opera su volumen diario promedio en los primeros 15–30 minutos. Mejor si venía lateral 3–6 meses y, si el catalizador son resultados, con crecimiento de EPS y ventas de dos o tres dígitos y una sorpresa fuerte.</p>
  <p><b>Entrada.</b> Ruptura del opening range high: máximo de la primera vela de 1, 5 o 60 minutos. Se puede sumar durante el día si la acción se comporta bien. El setup se identifica en el after-hours o el premarket.</p>
  <p><b>Stop.</b> Mínimo del día. Si queda más ancho que 1 ADR (como mucho 1,5), se achica la posición o no se opera.</p>
  <p><b>Tamaño.</b> Riesgo de 0,25–1% de la cuenta por trade. Posiciones de 10–20%, nunca más de 25–30% en una sola acción de un día para otro.</p>
  <p><b>Salida.</b> Vender 1/3–1/2 a los 3–5 días y subir el stop a breakeven. El resto, con la MM10 o MM20: se sale en el primer cierre debajo, no en un toque intradiario.</p>
  <p><b>Mercado.</b> Operar con los índices en tendencia. Referencia práctica: EMA10 de QQQ sobre la EMA20.</p>
  <p><b>Estadística esperable.</b> Él declaró tasas de acierto de 25–35%: el sistema vive de pocos ganadores grandes.</p>
  </details>

  <details class="how"><summary>Cómo leer el score</summary>
  <p>Ordena las señales; no es una señal en sí misma. Tamaño del gap (20), volumen relativo (20), neglect: cuánto subió en los 3 meses previos (20), cierre dentro del rango del día (15) y catalizador/números: resultados en la fecha, sorpresa de EPS y crecimiento de ventas (25).</p>
  <p>Los fundamentals vienen de Yahoo y a veces faltan o llegan con demora. Si el score es alto sin datos de resultados, revisar el catalizador a mano antes de operar.</p>
  </details>
  <footer>Descargas: <a href="data/ep_today.csv">EPs del día</a> · <a href="data/nine_m.csv">9M</a> · <a href="data/delayed.csv">Delayed</a> · <a href="data/followup.csv">Seguimiento</a> · <a href="data/rejected.csv">Descartados</a> · <a href="data/signals.csv">Registro de señales</a> · <a href="data/stats.csv">Estadísticas</a></footer>
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
.prov{border-left:4px solid var(--warn);background:var(--warnb);color:var(--ink)}.prov strong{color:var(--warn);margin-right:6px}
.calcbar{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center}
.calcbar label{font-size:13px;color:var(--mut)}.calcbar input{width:110px;margin-left:6px;padding:4px 6px;border:1px solid var(--line);border-radius:4px;background:var(--bg);color:var(--ink)}
.calcbar small{flex-basis:100%;color:var(--mut)}
.warn-t{color:var(--warn)}.bad-t{color:var(--bad)}.qqq{font-weight:600;margin-top:6px!important}
.calc small{color:var(--mut)}
.how{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;color:var(--mut)}
.how summary{cursor:pointer;color:var(--ink);font-weight:600}
footer{padding:18px 0 40px;color:var(--mut);font-size:12px}footer a{color:var(--acc)}
@media (max-width:640px){h1{font-size:19px}section{padding:12px}}
"""

JS = """
(function(){
  const ids=['cap','rk','mx'];
  const get=k=>{try{return localStorage.getItem('ep_'+k)}catch(e){return null}};
  const put=(k,v)=>{try{localStorage.setItem('ep_'+k,v)}catch(e){}};
  ids.forEach(id=>{const el=document.getElementById(id);if(!el)return;const v=get(id);if(v!==null&&v!=='')el.value=v;
    el.addEventListener('input',()=>{put(id,el.value);calc();});});
  const fmt=n=>n.toLocaleString('es-AR',{maximumFractionDigits:0});
  function calc(){
    const cap=parseFloat((document.getElementById('cap')||{}).value),rk=parseFloat((document.getElementById('rk')||{}).value),
          mx=parseFloat((document.getElementById('mx')||{}).value);
    document.querySelectorAll('.calc').forEach(c=>{
      const e=parseFloat(c.dataset.e),s=parseFloat(c.dataset.s);
      if(!(cap>0)||!(rk>0)||!(e>s)){c.textContent='—';return;}
      const byRisk=cap*rk/100/(e-s),byCap=mx>0?cap*mx/100/e:Infinity,sh=Math.floor(Math.min(byRisk,byCap));
      const capped=byCap<byRisk;
      c.innerHTML=fmt(sh)+' acc · USD '+fmt(sh*e)+(capped?' <small>(tope '+mx+'%: riesgo '+(sh*(e-s)/cap*100).toFixed(2)+'%)</small>':'');
    });
  }
  calc();
})();
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
    for key, fname in (("ep", "ep_today"), ("nine_m", "nine_m"), ("delayed", "delayed"),
                       ("followup", "followup"), ("rejected", "rejected")):
        pd.DataFrame(ctx[key]).to_csv(out_dir / "data" / f"{fname}.csv", index=False)
    ctx["signals"].to_csv(out_dir / "data" / "signals.csv", index=False)
    st = ctx["stats_type"]
    (st if st is not None and not st.empty else pd.DataFrame()).to_csv(out_dir / "data" / "stats.csv")


# El plan de trade va adelante (es lo accionable); el detalle, a la derecha.
def _front(cols, n):
    base = [c for c in cols if c not in PLAN_COLS]
    return base[:n] + PLAN_COLS + base[n:]


EP_COLS = _front(EP_COLS, 5)
DELAYED_COLS = _front(DELAYED_COLS, 5)
