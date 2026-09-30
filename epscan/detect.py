"""Detección de Episodic Pivots sobre barras diarias."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def prep(df: pd.DataFrame, avg_window: int = 50) -> pd.DataFrame:
    d = df.copy()
    d["prev_close"] = d["Close"].shift(1)
    d["gap"] = d["Open"] / d["prev_close"] - 1
    d["chg"] = d["Close"] / d["prev_close"] - 1
    d["avgvol"] = d["Volume"].shift(1).rolling(avg_window, min_periods=20).mean()
    d["rvol"] = d["Volume"] / d["avgvol"].replace(0, np.nan)
    d["dollar_vol"] = d["Close"] * d["Volume"]
    for w in (10, 20, 50):
        d[f"sma{w}"] = d["Close"].rolling(w).mean()
    # ADR% de las 20 ruedas PREVIAS (sin el día del evento, que infla el rango)
    d["adr"] = (d["High"] / d["Low"] - 1).rolling(20, min_periods=10).mean().shift(1)
    d["ema10"] = d["Close"].ewm(span=10, adjust=False).mean()
    d["ema20"] = d["Close"].ewm(span=20, adjust=False).mean()
    rng = d["High"] - d["Low"]
    d["close_pos"] = np.where(rng > 0, (d["Close"] - d["Low"]) / rng.where(rng > 0, 1), 0.5)
    return d


def ep_mask(d: pd.DataFrame, cfg: dict) -> pd.Series:
    m = (
        (d["gap"] >= cfg["min_gap"])
        & (d["rvol"] >= cfg["min_rvol"])
        & (d["Close"] >= cfg["min_price"])
        & (d["dollar_vol"] >= cfg["min_dollar_vol"])
    )
    if cfg.get("require_hold", True):
        m &= d["Close"] >= d["prev_close"]
    return m.fillna(False)


def nine_m_mask(d: pd.DataFrame, cfg: dict) -> pd.Series:
    m = (
        (d["Volume"] >= cfg["min_volume"])
        & (d["chg"] >= cfg["min_change"])
        & (d["rvol"] >= cfg["min_rvol"])
        & (d["Close"] >= cfg["min_price"])
    )
    return m.fillna(False)


def neglect_at(d: pd.DataFrame, i: int, cfg: dict) -> dict:
    """Métricas de 'olvido' medidas el día previo al evento i."""
    lb = cfg["lookback"]
    c = d["Close"].to_numpy()
    out = dict(ret_3m=np.nan, ext_sma50=np.nan, off_52wh=np.nan, neglected=None)
    if i < 1:
        return out
    j = i - 1
    if j - lb >= 0:
        out["ret_3m"] = c[j] / c[j - lb] - 1
    sma50 = d["sma50"].iat[j]
    if pd.notna(sma50) and sma50 > 0:
        out["ext_sma50"] = c[j] / sma50 - 1
    hi = d["High"].iloc[max(0, i - 252):i].max()
    if pd.notna(hi) and hi > 0:
        out["off_52wh"] = c[j] / hi - 1
    if not math.isnan(out["ret_3m"]):
        ext_ok = math.isnan(out["ext_sma50"]) or out["ext_sma50"] <= cfg["max_ext_sma50"]
        in_band = cfg.get("min_ret", -1.0) <= out["ret_3m"] <= cfg["max_ret"]
        out["neglected"] = bool(in_band and ext_ok)
    return out


def quality_reasons(d: pd.DataFrame, i: int, q: dict, day1: bool = True) -> list[str]:
    """Motivos por los que el evento i NO califica como EP de calidad (lista vacía = pasa)."""
    reasons = []
    c = d["Close"].iat[i]
    if c < q["min_price"]:
        reasons.append(f"Precio {c:.2f} < {q['min_price']:.0f}")
    if day1 and d["close_pos"].iat[i] < q["min_close_pos"]:
        reasons.append(f"Cierre débil ({d['close_pos'].iat[i] * 100:.0f}% del rango)")
    if i >= 1:
        hi = d["High"].iloc[max(0, i - 252):i].max()
        pc = d["Close"].iat[i - 1]
        if pd.notna(hi) and hi > 0 and pc / hi - 1 < q["max_off_52wh"]:
            reasons.append(f"Acción destruida ({(pc / hi - 1) * 100:.0f}% vs máx. 52 sem.)")
        w = d.iloc[max(0, i - q["runup_window"]):i]
        if len(w) >= 3 and w["Low"].min() > 0:
            runup = w["High"].max() / w["Low"].min() - 1
            if runup > q["max_prior_runup"]:
                reasons.append(f"Pump previo (+{runup * 100:.0f}% en {q['runup_window']} ruedas)")
    return reasons


def event_row(d: pd.DataFrame, i: int, ticker: str, neg_cfg: dict) -> dict:
    r = d.iloc[i]
    row = dict(
        ticker=ticker, date=d.index[i].date().isoformat(),
        open=r["Open"], high=r["High"], low=r["Low"], close=r["Close"],
        prev_close=r["prev_close"], gap=r["gap"], chg=r["chg"], rvol=r["rvol"],
        volume=r["Volume"], dollar_vol=r["dollar_vol"], close_pos=r["close_pos"],
        stop_ref=r["Low"], risk_pct=(r["Close"] / r["Low"] - 1) if r["Low"] > 0 else np.nan,
        adr=r["adr"],
    )
    row.update(neglect_at(d, i, neg_cfg))
    return row


def scan_ticker(ticker: str, df: pd.DataFrame, asof: pd.Timestamp, cfg) -> dict:
    """Devuelve las señales de un ticker al cierre `asof`."""
    res = dict(ep=[], nine_m=[], delayed=[], followup=[], rejected=[])
    if df is None or len(df) < 30 or df.index[-1] != asof:
        return res
    d = prep(df, cfg.EP["avg_vol_window"])
    n = len(d)
    t = n - 1
    em = ep_mask(d, cfg.EP).to_numpy()
    c = d["Close"].to_numpy()
    q = cfg.QUALITY
    ok_cache: dict[tuple[int, bool], bool] = {}

    def passes(i: int, day1: bool) -> bool:
        if (i, day1) not in ok_cache:
            ok_cache[(i, day1)] = not quality_reasons(d, i, q, day1)
        return ok_cache[(i, day1)]

    # 1) EP del día  /  2) 9M EP (si no es ya un EP clásico)
    kind = "EP" if em[t] else "9M" if nine_m_mask(d, cfg.NINE_M).iat[t] else None
    if kind:
        row = event_row(d, t, ticker, cfg.NEGLECT)
        row["is_9m"] = bool(d["Volume"].iat[t] >= cfg.NINE_M["min_volume"])
        reasons = quality_reasons(d, t, q, day1=True)
        if reasons:
            res["rejected"].append({**row, "kind": kind, "reasons": " · ".join(reasons)})
        else:
            res["ep" if kind == "EP" else "nine_m"].append(row)

    # 3) Seguimiento de EPs recientes (incluye el de hoy), sólo los que pasaron los filtros
    lo = max(1, t - cfg.FOLLOWUP["days"])
    for e in np.flatnonzero(em[lo:t + 1]) + lo:
        if not passes(int(e), True):
            continue
        post_low = d["Low"].iloc[e + 1:t + 1].min() if e < t else np.nan
        post_close_min = d["Close"].iloc[e + 1:t + 1].min() if e < t else np.nan
        lod = d["Low"].iat[e]
        fu = dict(
            ticker=ticker, ep_date=d.index[e].date().isoformat(), days=int(t - e),
            ep_gap=d["gap"].iat[e], ep_rvol=d["rvol"].iat[e], ep_close=c[e], ep_lod=lod,
            close=c[t], ret_since=c[t] / c[e] - 1,
            max_gain=d["High"].iloc[e:t + 1].max() / c[e] - 1,
            sma10=d["sma10"].iat[t], sma20=d["sma20"].iat[t],
            above_sma10=bool(c[t] >= d["sma10"].iat[t]) if pd.notna(d["sma10"].iat[t]) else None,
            above_sma20=bool(c[t] >= d["sma20"].iat[t]) if pd.notna(d["sma20"].iat[t]) else None,
            lod_broken=bool(post_close_min < lod) if e < t else False,
            gap_filled=bool(post_low <= d["prev_close"].iat[e]) if e < t else False,
        )
        if fu["lod_broken"]:
            fu["status"] = "Perdió LOD"
        elif fu["above_sma10"]:
            fu["status"] = "Sobre MM10"
        elif fu["above_sma20"]:
            fu["status"] = "Entre MM10 y MM20"
        else:
            fu["status"] = "Bajo MM20"
        res["followup"].append(fu)

    # 4) Delayed EP: EP de hace [min_days, max_days] ruedas que sostiene el gap
    dc = cfg.DELAYED
    if not em[t]:
        lo, hi = max(1, t - dc["max_days"]), t - dc["min_days"]
        cands = [int(e) for e in (np.flatnonzero(em[lo:hi + 1]) + lo if hi >= lo else [])
                 if passes(int(e), False)]
        if len(cands) and c[t] >= q["min_price"]:
            e = int(cands[-1])
            row = _delayed(d, e, t, ticker, cfg)
            if row:
                res["delayed"].append(row)
    return res


def _delayed(d: pd.DataFrame, e: int, t: int, ticker: str, cfg) -> dict | None:
    dc = cfg.DELAYED
    c = d["Close"].to_numpy()
    post = d.iloc[e + 1:t + 1]
    held_gap = post["Close"].min() > d["prev_close"].iat[e]
    held_lod = post["Low"].min() >= d["Low"].iat[e]
    if not held_gap:
        return None
    hi_since = d["High"].iloc[e:t + 1].max()
    from_high = c[t] / hi_since - 1

    # Ventana previa para el breakout (sólo ruedas posteriores al EP)
    b0 = max(e + 1, t - dc["breakout_window"])
    prior = d.iloc[b0:t]
    breakout = len(prior) >= 2 and c[t] > prior["High"].max()

    tw = d.iloc[max(e + 1, t - dc["tight_window"] + 1):t + 1]
    tight_range = (tw["High"].max() - tw["Low"].min()) / c[t]
    tight = len(tw) >= 3 and tight_range <= dc["max_tight_range"]
    near = from_high >= -dc["max_from_high"]

    if breakout:
        kind, stop, trigger = "Breakout", prior["Low"].min(), c[t]
    elif tight and near:
        kind, stop, trigger = "Setup", tw["Low"].min(), tw["High"].max()
    else:
        return None

    ev = event_row(d, e, ticker, cfg.NEGLECT)
    return dict(
        ticker=ticker, kind=kind, date=d.index[t].date().isoformat(),
        ep_date=ev["date"], days=int(t - e), ep_gap=ev["gap"], ep_rvol=ev["rvol"],
        ep_close_pos=ev["close_pos"], ret_3m=ev["ret_3m"], ext_sma50=ev["ext_sma50"],
        neglected=ev["neglected"], held_lod=bool(held_lod),
        close=c[t], chg=d["chg"].iat[t], rvol=d["rvol"].iat[t], from_high=from_high,
        tight_range=tight_range,
        vol_dryup=tw["Volume"].mean() / d["Volume"].iat[e] if d["Volume"].iat[e] > 0 else np.nan,
        ret_since_ep=c[t] / c[e] - 1, stop_ref=stop, trigger=trigger, adr=d["adr"].iat[t],
        risk_pct=c[t] / stop - 1 if stop > 0 else np.nan,
        dollar_vol=d["dollar_vol"].iat[t],
    )


# ── Contexto de mercado ─────────────────────────────────────────────────────
def market_regime(prices: dict[str, pd.DataFrame], indices: list[str]) -> dict:
    rows, pts, total = [], 0, 0
    for sym in indices:
        df = prices.get(sym)
        if df is None or len(df) < 60:
            continue
        c = df["Close"]
        s20, s50 = c.rolling(20).mean(), c.rolling(50).mean()
        a20, a50 = bool(c.iat[-1] > s20.iat[-1]), bool(c.iat[-1] > s50.iat[-1])
        up20 = bool(s20.iat[-1] > s20.iat[-6])
        e10, e20 = c.ewm(span=10, adjust=False).mean(), c.ewm(span=20, adjust=False).mean()
        pts += a20 + a50 + up20
        total += 3
        rows.append(dict(symbol=sym, close=c.iat[-1], chg=c.iat[-1] / c.iat[-2] - 1,
                         above_sma20=a20, above_sma50=a50, sma20_up=up20,
                         ema10_gt_ema20=bool(e10.iat[-1] > e20.iat[-1]),
                         ret_1m=c.iat[-1] / c.iat[-22] - 1))
    if not total:
        return dict(label="Sin datos", score=None, indices=rows)
    share = pts / total
    label = "Favorable" if share >= 0.75 else "Neutral" if share >= 0.45 else "Desfavorable"
    qqq = next((r for r in rows if r["symbol"] == "QQQ"), None)
    return dict(label=label, score=share, indices=rows,
                qqq_ema_ok=None if qqq is None else qqq["ema10_gt_ema20"])


# ── Plan de trade ───────────────────────────────────────────────────────────
def plan(row: dict, kind: str, tc: dict) -> dict:
    """Entrada de referencia, stop natural (LOD o consolidación) y stop con tope ADR."""
    entry = row["trigger"] if kind == "Setup" else row["close"]
    natural = row["stop_ref"]
    adr = row.get("adr")
    out = dict(plan_entry=entry, plan_natural_stop=natural)
    if entry is None or natural is None or not entry > 0:
        return out
    width = (entry - natural) / entry
    out["stop_adr"] = width / adr if adr and adr > 0 else np.nan
    cap = entry * (1 - tc["max_stop_adr"] * adr) if adr and adr > 0 else natural
    stop = max(natural, cap)
    out.update(plan_stop=stop, plan_stop_pct=(entry - stop) / entry,
               stop_capped=bool(stop > natural + 1e-9))
    return out
