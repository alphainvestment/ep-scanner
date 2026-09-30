"""Registro histórico de señales y simulación de resultados (en R) con las reglas de Qullamaggie.

Supuestos (declarados en el reporte):
  - Entrada: apertura de la rueda siguiente a la señal (el scanner corre al cierre; no ve el ORH).
  - Stop inicial: el de la señal (LOD o mínimo de la consolidación), ajustado para que no quede
    más ancho que `max_stop_adr` × ADR desde la entrada real. Si la apertura ya está debajo: 'invalid'.
  - Parcial: `partial_fraction` al cierre del primer día entre `partial_days` en que esté en
    ganancia; después el stop sube a breakeven.
  - Resto: sale en el primer cierre debajo de la media de `trail_ma` ruedas, una vez que esa
    media superó el stop inicial. Tope de seguridad: `max_bars` ruedas.
  - Si una rueda abre debajo del stop, sale en la apertura; si lo toca en el día, sale en el stop.
    Se asume que el stop se ejecuta antes que cualquier máximo del mismo día.
  - Sin comisiones ni slippage.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = [
    "signal_date", "ticker", "type", "score", "regime", "ref_close", "stop", "adr",
    "status", "entry_date", "entry", "stop_used", "partial_date", "exit_date", "exit", "exit_reason", "r",
    "ret_5", "ret_10", "ret_20", "mfe_r", "mae_r", "bars",
]


def load(path: Path) -> pd.DataFrame:
    if path.exists():
        df = pd.read_csv(path, dtype={"ticker": str, "signal_date": str})
        for c in COLUMNS:
            if c not in df.columns:
                df[c] = np.nan
        return df[COLUMNS]
    return pd.DataFrame(columns=COLUMNS)


def append_signals(log: pd.DataFrame, new_rows: list[dict]) -> pd.DataFrame:
    if not new_rows:
        return log
    new = pd.DataFrame(new_rows)
    for c in COLUMNS:
        if c not in new.columns:
            new[c] = np.nan
    new["status"] = "pending"
    key = lambda df: df["signal_date"].astype(str) + "|" + df["ticker"] + "|" + df["type"]
    new = new[~key(new).isin(set(key(log)))] if len(log) else new
    if log.empty:
        return new[COLUMNS].reset_index(drop=True)
    return pd.concat([log, new[COLUMNS]], ignore_index=True)


def evaluate(sig: dict, df: pd.DataFrame, tc: dict, horizons: list[int]) -> dict:
    """Recalcula el resultado de una señal con los precios disponibles."""
    out = {}
    sd = pd.Timestamp(sig["signal_date"])
    after = df[df.index > sd].iloc[: tc["max_bars"]]
    if after.empty:
        return dict(status="pending")
    entry = float(after["Open"].iat[0])
    stop0 = float(sig["stop"])
    adr = pd.to_numeric(sig.get("adr"), errors="coerce")
    if pd.notna(adr) and adr > 0:
        stop0 = max(stop0, entry * (1 - tc["max_stop_adr"] * adr))
    risk = entry - stop0
    out.update(entry_date=after.index[0].date().isoformat(), entry=entry, stop_used=stop0)
    if risk <= 0:
        return dict(out, status="invalid", exit_reason="abrió bajo el stop")

    closes = after["Close"].to_numpy()
    for h in horizons:
        out[f"ret_{h}"] = closes[h - 1] / entry - 1 if len(closes) >= h else np.nan
    ma = df["Close"].rolling(tc["trail_ma"]).mean().reindex(after.index).to_numpy()

    d_min, d_max = tc["partial_days"]
    frac = tc["partial_fraction"]
    rem, realized, stop = 1.0, 0.0, stop0
    partial_date = exit_px = exit_i = reason = None
    for k in range(len(after)):
        o, hi, lo, c = (float(after[x].iat[k]) for x in ("Open", "High", "Low", "Close"))
        if k > 0 and o <= stop:
            exit_px, exit_i, reason = o, k, "gap bajo stop"
            break
        if lo <= stop:
            exit_px, exit_i = stop, k
            reason = "breakeven" if partial_date and stop >= entry else "stop"
            break
        day = k + 1
        if partial_date is None and d_min <= day <= d_max and c > entry:
            realized += frac * (c - entry) / risk
            rem -= frac
            stop = max(stop, entry)
            partial_date = after.index[k].date().isoformat()
        if pd.notna(ma[k]) and ma[k] > stop0 and c < ma[k]:
            exit_px, exit_i, reason = c, k, f"cierre < MM{tc['trail_ma']}"
            break
    if exit_px is None and len(after) >= tc["max_bars"]:
        exit_px, exit_i, reason = closes[-1], len(after) - 1, f"tope {tc['max_bars']} ruedas"

    last_i = exit_i if exit_i is not None else len(after) - 1
    path = after.iloc[: last_i + 1]
    out.update(mfe_r=(path["High"].max() - entry) / risk, mae_r=(path["Low"].min() - entry) / risk,
               bars=last_i + 1, partial_date=partial_date)
    if exit_px is None:
        out.update(status="open", r=realized + rem * (closes[-1] - entry) / risk)
    else:
        out.update(status="closed", exit=float(exit_px), exit_reason=reason,
                   exit_date=after.index[exit_i].date().isoformat(),
                   r=realized + rem * (exit_px - entry) / risk)
    return out


def update(log: pd.DataFrame, prices: dict[str, pd.DataFrame], tc: dict,
           horizons: list[int]) -> pd.DataFrame:
    log = log.copy().astype({c: "object" for c in
                             ["status", "entry_date", "exit_date", "exit_reason", "partial_date"]})
    for i, sig in log.iterrows():
        if sig["status"] in ("closed", "invalid"):
            continue
        df = prices.get(sig["ticker"])
        if df is None:
            continue
        for k, v in evaluate(sig.to_dict(), df, tc, horizons).items():
            log.at[i, k] = v
    return log


def stats(log: pd.DataFrame, horizons: list[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Estadísticas por tipo de señal y por tipo × contexto de mercado (sólo señales cerradas)."""
    closed = log[log["status"] == "closed"].copy()
    closed["r"] = pd.to_numeric(closed["r"], errors="coerce")

    def agg(g: pd.DataFrame) -> pd.Series:
        r = g["r"].dropna()
        pos, neg = r[r > 0].sum(), -r[r < 0].sum()
        s = dict(
            n=len(r), win_rate=(r > 0).mean() if len(r) else np.nan,
            avg_r=r.mean(), median_r=r.median(),
            avg_win_r=r[r > 0].mean(), avg_loss_r=r[r <= 0].mean(),
            profit_factor=pos / neg if neg > 0 else np.nan,
            best_r=r.max(),
        )
        for h in horizons:
            s[f"avg_ret_{h}"] = pd.to_numeric(g[f"ret_{h}"], errors="coerce").mean()
        return pd.Series(s)

    if closed.empty:
        return pd.DataFrame(), pd.DataFrame()
    by_type = closed.groupby("type").apply(agg, include_groups=False) if _pd_has_include_groups() \
        else closed.groupby("type").apply(agg)
    total = agg(closed).to_frame("TODAS").T
    by_type = pd.concat([by_type, total])
    by_regime = closed.groupby(["type", "regime"]).apply(agg, include_groups=False) \
        if _pd_has_include_groups() else closed.groupby(["type", "regime"]).apply(agg)
    return by_type, by_regime


def _pd_has_include_groups() -> bool:
    major, minor = (int(x) for x in pd.__version__.split(".")[:2])
    return (major, minor) >= (2, 2)
