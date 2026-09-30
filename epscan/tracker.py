"""Registro histórico de señales y cálculo de resultados (en R) para medir el edge real.

Supuestos de la simulación (declarados en el reporte):
  - Entrada: apertura de la rueda siguiente a la señal.
  - Stop: `stop` registrado con la señal. Si la apertura de entrada ya está debajo, la señal queda 'invalid'.
  - Si una rueda abre debajo del stop, la salida es la apertura (gap en contra); si no, el stop.
  - Si no toca el stop, sale al cierre de la rueda N° `horizon` (contando la de entrada).
  - Mismo día: si una rueda toca el stop se asume que el stop se ejecutó antes que cualquier máximo.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = [
    "signal_date", "ticker", "type", "score", "regime", "ref_close", "stop",
    "status", "entry_date", "entry", "exit_date", "exit", "exit_reason", "r",
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


def evaluate(sig: dict, df: pd.DataFrame, horizon: int, horizons: list[int]) -> dict:
    """Recalcula el resultado de una señal con los precios disponibles."""
    out = {}
    sd = pd.Timestamp(sig["signal_date"])
    after = df[df.index > sd]
    if after.empty:
        return dict(status="pending")
    stop = float(sig["stop"])
    entry = float(after["Open"].iat[0])
    risk = entry - stop
    out.update(entry_date=after.index[0].date().isoformat(), entry=entry)
    if risk <= 0:
        return dict(out, status="invalid", exit_reason="abrió bajo el stop")

    bars = after.iloc[:horizon]
    closes = bars["Close"].to_numpy()
    for h in horizons:
        out[f"ret_{h}"] = closes[h - 1] / entry - 1 if len(closes) >= h else np.nan

    exit_px, exit_i, reason = None, None, None
    for k in range(len(bars)):
        o, lo = bars["Open"].iat[k], bars["Low"].iat[k]
        if k > 0 and o <= stop:
            exit_px, exit_i, reason = o, k, "gap bajo stop"
            break
        if lo <= stop:
            exit_px, exit_i, reason = stop, k, "stop"
            break
    if exit_px is None and len(bars) >= horizon:
        exit_px, exit_i, reason = closes[horizon - 1], horizon - 1, f"tiempo ({horizon} ruedas)"

    last_i = exit_i if exit_i is not None else len(bars) - 1
    path = bars.iloc[:last_i + 1]
    out["mfe_r"] = (path["High"].max() - entry) / risk
    out["mae_r"] = (path["Low"].min() - entry) / risk
    out["bars"] = last_i + 1
    if exit_px is None:
        out.update(status="open", r=(closes[-1] - entry) / risk)
    else:
        out.update(status="closed", exit=float(exit_px), exit_reason=reason,
                   exit_date=bars.index[exit_i].date().isoformat(), r=(exit_px - entry) / risk)
    return out


def update(log: pd.DataFrame, prices: dict[str, pd.DataFrame], horizon: int,
           horizons: list[int]) -> pd.DataFrame:
    log = log.copy().astype({c: "object" for c in ["status", "entry_date", "exit_date", "exit_reason"]})
    for i, sig in log.iterrows():
        if sig["status"] in ("closed", "invalid"):
            continue
        df = prices.get(sig["ticker"])
        if df is None:
            continue
        for k, v in evaluate(sig.to_dict(), df, horizon, horizons).items():
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
