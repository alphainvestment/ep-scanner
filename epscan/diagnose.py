"""Diagnóstico: evalúa cada regla del scanner sobre un ticker y una fecha.

Uso:  python run.py --diagnose MRNA 2026-08-19
Responde "¿por qué apareció / no apareció X ese día?" sin tocar el reporte ni el registro.
"""
from __future__ import annotations

import os

import pandas as pd

from . import detect
from .score import score


def _u(x):
    return "—" if x is None or pd.isna(x) else f"{x * 100:.1f}%"


def _p(x, d=1):
    return "—" if x is None or pd.isna(x) else f"{x * 100:+.{d}f}%"


def diagnose(cfg, fetcher, ticker: str, day: str) -> str:
    raw = fetcher.download([ticker], "2y").get(ticker)
    if raw is None:
        return f"No se pudieron bajar datos de {ticker}."
    d_all = detect.prep(raw, cfg.EP["avg_vol_window"])
    target = pd.Timestamp(day)
    if target not in d_all.index:
        later = d_all.index[d_all.index >= target]
        if not len(later):
            return f"No hay rueda de {ticker} en o después de {day}."
        target = later[0]
    i = d_all.index.get_loc(target)
    d = d_all.iloc[: i + 1]
    r = d.iloc[-1]
    q, e, n9 = cfg.QUALITY, cfg.EP, cfg.NINE_M
    neg = detect.neglect_at(d, i, cfg.NEGLECT)

    def row(name, value, rule, ok):
        return f"| {name} | {value} | {rule} | {'✅' if ok else '❌'} |"

    lines = [f"## Diagnóstico {ticker} · {target.date()}",
             f"Apertura {r['Open']:.2f} · Máx {r['High']:.2f} · Mín {r['Low']:.2f} · Cierre {r['Close']:.2f} · "
             f"Cierre previo {r['prev_close']:.2f} · Volumen {r['Volume'] / 1e6:.1f}M", "",
             "### Reglas de EP del día", "| Regla | Valor | Umbral | Pasa |", "|---|---|---|---|",
             row("Gap", _p(r["gap"]), f"≥ {e['min_gap'] * 100:.0f}%", r["gap"] >= e["min_gap"]),
             row("RVol", f"{r['rvol']:.1f}x", f"≥ {e['min_rvol']:.0f}x", r["rvol"] >= e["min_rvol"]),
             row("USD operados", f"{r['dollar_vol'] / 1e6:,.0f}M", f"≥ {e['min_dollar_vol'] / 1e6:.0f}M",
                 r["dollar_vol"] >= e["min_dollar_vol"]),
             row("No devolvió el gap", _p(r["chg"]), "cierre ≥ cierre previo", r["Close"] >= r["prev_close"]),
             "", "### Filtros de calidad", "| Regla | Valor | Umbral | Pasa |", "|---|---|---|---|",
             row("Precio", f"{r['Close']:.2f}", f"≥ {q['min_price']:.0f}", r["Close"] >= q["min_price"]),
             row("Cierre en el rango", f"{r['close_pos'] * 100:.0f}%", f"≥ {q['min_close_pos'] * 100:.0f}%",
                 r["close_pos"] >= q["min_close_pos"]),
             row("vs máx. 52 sem. (día previo)", _p(neg["off_52wh"]), f"≥ {q['max_off_52wh'] * 100:.0f}%",
                 pd.isna(neg["off_52wh"]) or neg["off_52wh"] >= q["max_off_52wh"])]
    w = d.iloc[max(0, i - q["runup_window"]):i]
    runup = w["High"].max() / w["Low"].min() - 1 if len(w) else float("nan")
    lines.append(row(f"Suba en {q['runup_window']} ruedas previas", _p(runup, 0),
                     f"≤ {q['max_prior_runup'] * 100:.0f}%", not runup > q["max_prior_runup"]))
    lines += ["", "### Neglect (no descarta, suma al score)", "| Métrica | Valor | Banda | Cumple |", "|---|---|---|---|",
              row("Variación 3 meses previa", _p(neg["ret_3m"]),
                  f"{cfg.NEGLECT['min_ret'] * 100:+.0f}% a {cfg.NEGLECT['max_ret'] * 100:+.0f}%",
                  bool(neg["neglected"]) or (neg["ret_3m"] is not None and
                                             cfg.NEGLECT['min_ret'] <= neg["ret_3m"] <= cfg.NEGLECT['max_ret'])),
              row("Distancia a MM50 (día previo)", _p(neg["ext_sma50"]),
                  f"≤ {cfg.NEGLECT['max_ext_sma50'] * 100:.0f}%",
                  pd.isna(neg["ext_sma50"]) or neg["ext_sma50"] <= cfg.NEGLECT["max_ext_sma50"])]
    is_ep = bool(detect.ep_mask(d, e).iat[-1])
    is_9m = bool(detect.nine_m_mask(d, n9).iat[-1])
    reasons = detect.quality_reasons(d, i, q, day1=True)
    ev = {**detect.event_row(d, i, ticker, cfg.NEGLECT)}
    verdict = ("EP del día" if is_ep else "9M EP" if is_9m else "no califica por gap/volumen")
    if (is_ep or is_9m) and reasons:
        verdict = f"DESCARTADO ({' · '.join(reasons)})"
    pl = detect.plan(ev, "EP", cfg.TRADE)
    adr = ev.get("adr")
    lines += ["", "### Plan de trade (visto al cierre)", "| Dato | Valor |", "|---|---|",
              f"| ADR 20 ruedas previas | {_u(adr)} |",
              f"| Entrada de referencia (cierre) | {pl.get('plan_entry', float('nan')):.2f} |",
              f"| Stop natural (LOD) | {pl.get('plan_natural_stop', float('nan')):.2f} |",
              f"| Ancho del stop natural | {pl.get('stop_adr', float('nan')):.1f} ADR "
              f"({'operable' if pl.get('stop_adr', 99) <= 1.5 else 'demasiado ancho: achicar o esperar un delayed EP'}) |",
              f"| Stop del plan (tope {cfg.TRADE['max_stop_adr']:g} ADR) | {pl.get('plan_stop', float('nan')):.2f} "
              f"(riesgo {_u(pl.get('plan_stop_pct'))}) |"]
    if adr is not None and adr > 0:
        lod, op = r["Low"], r["Open"]
        lines.append(f"| Día 1 con entrada en la apertura ({op:.2f}) y stop LOD | riesgo "
                     f"{(op - lod) / op * 100:.1f}% = {(op - lod) / op / adr:.1f} ADR |")
    lines += ["", f"**Resultado:** {verdict} · Score técnico (sin fundamentals): {score(ev)}"]
    out = "\n".join(lines)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(out + "\n")
    return out
