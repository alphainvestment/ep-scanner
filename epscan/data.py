"""Descarga de precios y fundamentals vía yfinance, con reintentos por bloques."""
from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

log = logging.getLogger("epscan")

FIELDS = ["Open", "High", "Low", "Close", "Volume"]


def _clean(df: pd.DataFrame) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    df = df[[c for c in FIELDS if c in df.columns]].copy()
    if len(df.columns) < 5:
        return None
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[(df["Close"] > 0) & (df["Open"] > 0)]
    if df.empty:
        return None
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df["Volume"] = df["Volume"].fillna(0).astype(float)
    return df


class YahooFetcher:
    """Implementación real. Los tests usan un fetcher falso con la misma interfaz."""

    def __init__(self, cfg_download: dict):
        import yfinance as yf  # import diferido: los tests no lo necesitan
        self.yf = yf
        self.cfg = cfg_download

    # ── precios ──
    def _download_chunk(self, tickers: list[str], period: str) -> dict[str, pd.DataFrame]:
        raw = self.yf.download(
            tickers, period=period, interval="1d", group_by="ticker",
            auto_adjust=False, threads=True, progress=False, multi_level_index=True,
        )
        out = {}
        if raw is None or raw.empty:
            return out
        for t in tickers:
            try:
                sub = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                continue
            df = _clean(sub)
            if df is not None and len(df) > 5:
                out[t] = df
        return out

    def download(self, tickers: list[str], period: str | None = None) -> dict[str, pd.DataFrame]:
        period = period or self.cfg["period"]
        size, pause = self.cfg["chunk_size"], self.cfg["pause_s"]
        tickers = list(dict.fromkeys(tickers))
        data: dict[str, pd.DataFrame] = {}
        pending = tickers
        for attempt in range(self.cfg["retries"] + 1):
            if not pending:
                break
            if attempt:
                size = max(20, size // 3)
                log.info("Reintento %d: %d tickers sin datos", attempt, len(pending))
                time.sleep(pause * 5)
            for i in range(0, len(pending), size):
                chunk = pending[i:i + size]
                try:
                    data.update(self._download_chunk(chunk, period))
                except Exception as e:  # rate limit u otros errores de red
                    log.warning("Bloque %d falló: %s", i // size, e)
                    time.sleep(pause * 10)
                time.sleep(pause)
            pending = [t for t in pending if t not in data]
        log.info("Descargados %d/%d tickers", len(data), len(tickers))
        return data

    # ── fundamentals ──
    def fundamentals(self, ticker: str, event_date: pd.Timestamp, window_days: int) -> dict:
        tk = self.yf.Ticker(ticker)
        out: dict = {}
        try:
            info = tk.info or {}
            out.update(
                name=info.get("shortName") or info.get("longName"),
                sector=info.get("sector"),
                industry=info.get("industry"),
                mkt_cap=info.get("marketCap"),
                float_shares=info.get("floatShares"),
                short_pct_float=info.get("shortPercentOfFloat"),
                rev_growth=info.get("revenueGrowth"),
                eps_growth_q=info.get("earningsQuarterlyGrowth"),
            )
        except Exception as e:
            log.debug("%s info: %s", ticker, e)

        # ¿Hubo resultados justo antes del gap?
        try:
            ed = tk.get_earnings_dates(limit=8)
            if ed is not None and not ed.empty:
                idx = pd.to_datetime(ed.index)
                if idx.tz is not None:
                    idx = idx.tz_convert("America/New_York").tz_localize(None)
                days = idx.normalize()
                ev = pd.Timestamp(event_date).normalize()
                mask = (days <= ev) & (days >= ev - pd.Timedelta(days=window_days))
                if mask.any():
                    row = ed[mask].iloc[0]
                    out["earnings_catalyst"] = True
                    out["earnings_date"] = days[mask][0].date().isoformat()
                    for col in ed.columns:
                        cl = col.lower()
                        if "surprise" in cl:
                            out["eps_surprise_pct"] = _num(row[col])
                        elif "estimate" in cl:
                            out["eps_est"] = _num(row[col])
                        elif "reported" in cl:
                            out["eps_act"] = _num(row[col])
                else:
                    out["earnings_catalyst"] = False
        except Exception as e:
            log.debug("%s earnings: %s", ticker, e)

        # Aceleración de ventas: YoY del último trimestre vs el anterior
        try:
            qis = tk.quarterly_income_stmt
            if qis is not None and "Total Revenue" in qis.index:
                rev = qis.loc["Total Revenue"].dropna().sort_index(ascending=False).astype(float)
                if len(rev) >= 5 and rev.iloc[4] > 0:
                    out["rev_yoy_q0"] = rev.iloc[0] / rev.iloc[4] - 1
                if len(rev) >= 6 and rev.iloc[5] > 0:
                    out["rev_yoy_q1"] = rev.iloc[1] / rev.iloc[5] - 1
        except Exception as e:
            log.debug("%s income stmt: %s", ticker, e)
        return out


def _num(x):
    try:
        v = float(x)
        return None if np.isnan(v) else v
    except (TypeError, ValueError):
        return None
