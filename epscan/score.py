"""Score 0-100 de calidad del EP. Es una ayuda para ordenar, no una señal en sí misma.

Componentes (máximo):
  Tamaño del movimiento  20  gap (o variación, en 9M) hasta 25%
  Volumen relativo       20  escala logarítmica hasta 10x
  Neglect                20  suba de 3 meses previa: 20 pts si <= 10%, 0 si >= 50%
  Cierre en el rango     15  cerrar en el máximo del día = 15
  Catalizador / números  25  resultados en la fecha (10) + sorpresa EPS (hasta 5) + crecimiento de ventas (hasta 10)
"""
from __future__ import annotations

import math


def _f(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def score(row: dict) -> int:
    pts = 0.0
    move = max(_f(row.get("gap")) or 0, _f(row.get("chg")) or 0)
    pts += 20 * min(max(move, 0) / 0.25, 1)

    rv = _f(row.get("rvol")) or 0
    pts += 20 * min(max(math.log10(rv), 0), 1) if rv > 0 else 0

    r3 = _f(row.get("ret_3m"))
    if r3 is not None:
        pts += 20 * min(max((0.50 - r3) / 0.40, 0), 1)

    cp = _f(row.get("close_pos"))
    if cp is not None:
        pts += 15 * min(max(cp, 0), 1)

    fund = 0.0
    if row.get("earnings_catalyst"):
        fund += 10
    sp = _f(row.get("eps_surprise_pct"))
    if sp is not None and sp > 0:
        fund += 5 * min(sp / 25, 1)
    g = _f(row.get("rev_yoy_q0"))
    if g is None:
        g = _f(row.get("rev_growth"))
    if g is not None and g > 0:
        fund += 10 * min(g / 0.50, 1)
    pts += min(fund, 25)
    return int(round(pts))
