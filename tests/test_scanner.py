"""Tests offline con series sintéticas: python -m pytest tests  (o python tests/test_scanner.py)."""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as cfg  # noqa: E402
from epscan import detect, main, tracker  # noqa: E402

N = 260
DATES = pd.bdate_range(end="2026-09-30", periods=N)
RNG = np.random.default_rng(7)


def series(closes, vols, gaps=None, lows=None, highs=None, opens=None):
    c = np.asarray(closes, float)
    o = np.r_[c[0], c[:-1]] * (1 + RNG.normal(0, 0.002, len(c))) if opens is None else np.asarray(opens, float)
    h = np.maximum(o, c) * 1.01 if highs is None else np.asarray(highs, float)
    lo = np.minimum(o, c) * 0.99 if lows is None else np.asarray(lows, float)
    return pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": c, "Volume": np.asarray(vols, float)},
                        index=DATES)


def flat(p=20.0, vol=5e5, n=N, noise=0.004):
    return p * np.exp(np.cumsum(RNG.normal(0, noise, n))), np.full(n, vol) * RNG.uniform(0.8, 1.2, n)


def with_gap(df, i, gap, close_mult, vol, low_mult=0.99, high_mult=1.01):
    df = df.copy()
    pc = df["Close"].iat[i - 1]
    o = pc * (1 + gap)
    c = o * close_mult
    df.iloc[i, df.columns.get_loc("Open")] = o
    df.iloc[i, df.columns.get_loc("Close")] = c
    df.iloc[i, df.columns.get_loc("High")] = max(o, c) * high_mult
    df.iloc[i, df.columns.get_loc("Low")] = min(o, c) * low_mult
    df.iloc[i, df.columns.get_loc("Volume")] = vol
    # las ruedas siguientes arrancan desde el nuevo nivel
    if i + 1 < len(df):
        ratio = c / df["Close"].iat[i]
    return df


def shift_after(df, i, level):
    """Reescala las barras posteriores a i para que sigan desde `level`."""
    df = df.copy()
    if i + 1 >= len(df):
        return df
    base = df["Close"].iat[i + 1]
    k = level / base
    for col in ("Open", "High", "Low", "Close"):
        df.iloc[i + 1:, df.columns.get_loc(col)] *= k
    return df


def build_universe():
    u = {}
    # EP clásico sobre acción olvidada, hoy
    c, v = flat(20)
    u["NEGL"] = with_gap(series(c, v), N - 1, 0.20, 1.05, 6e6, low_mult=0.98, high_mult=1.005)
    # EP sobre acción ya extendida (+80% en 3 meses)
    c = np.r_[np.full(N - 70, 10.0), np.linspace(10, 18, 70)]
    u["EXTD"] = with_gap(series(c, np.full(N, 8e5)), N - 1, 0.12, 1.02, 7e6)
    # Gap que se devolvió entero
    c, v = flat(30)
    u["FADE"] = with_gap(series(c, v), N - 1, 0.15, 0.85, 7e6)
    # 9M: sin gap relevante, suba de 6% con 12M de volumen
    c, v = flat(40, 3e6)
    df = series(c, v)
    pc = df["Close"].iat[-2]
    df.iloc[-1] = [pc * 1.01, pc * 1.065, pc * 1.0, pc * 1.06, 12e6]
    u["NINE"] = df
    # Delayed breakout: EP hace 10 ruedas, consolida ajustado y hoy rompe
    c, v = flat(15, 6e5)
    df = with_gap(series(c, v), N - 11, 0.18, 1.04, 5e6)
    lvl = df["Close"].iat[N - 11]
    for k in range(N - 10, N - 1):
        df.iloc[k] = [lvl * 1.00, lvl * 1.02, lvl * 0.98, lvl * 1.005, 7e5]
    df.iloc[N - 1] = [lvl * 1.01, lvl * 1.07, lvl * 1.0, lvl * 1.06, 2.5e6]
    u["DELB"] = df
    # Delayed setup: EP hace 8 ruedas, consolidación ajustada, sin ruptura
    c, v = flat(25, 6e5)
    df = with_gap(series(c, v), N - 9, 0.14, 1.03, 5e6)
    lvl = df["Close"].iat[N - 9]
    for k in range(N - 8, N):
        df.iloc[k] = [lvl * 0.995, lvl * 1.01, lvl * 0.975, lvl * 0.99, 5e5]
    u["DELS"] = df
    # EP que después cubrió el gap: no debe ser delayed
    c, v = flat(12, 1e6)
    df = with_gap(series(c, v), N - 11, 0.15, 1.02, 9e6)
    pre = df["Close"].iat[N - 12]
    for k in range(N - 10, N):
        df.iloc[k] = [pre * 0.99, pre * 1.0, pre * 0.95, pre * 0.97, 1e6]
    u["FILL"] = df
    # EP viejo (hace 40 ruedas) que después sube: sirve para probar el registro de resultados
    c, v = flat(18, 7e5)
    df = with_gap(series(c, v), N - 41, 0.16, 1.03, 6e6)
    lvl = df["Close"].iat[N - 41]
    path = lvl * np.linspace(1.01, 1.45, 40)
    for j, k in enumerate(range(N - 40, N)):
        df.iloc[k] = [path[j] * 0.995, path[j] * 1.01, path[j] * 0.985, path[j], 8e5]
    u["OLD"] = df
    # Pump de microcap destruida (como LGHL/VBIO): de 400 a 4, gap +45% y cierre flojo
    c = np.r_[np.geomspace(400, 6, N - 60), np.linspace(6, 4.5, 60)]
    u["BUST"] = with_gap(series(c, np.full(N, 4e5)), N - 1, 0.45, 0.85, 2e7, low_mult=0.97, high_mult=1.3)
    # Ya bombeada en las ruedas previas (como MSGY): de 3 a 9 en 5 ruedas y otro gap
    c = np.r_[np.full(N - 6, 6.0), [7, 9, 13, 11, 12]]
    c = np.r_[c, [12.0]]
    u["PUMP"] = with_gap(series(c, np.full(N, 5e5)), N - 1, 0.12, 1.05, 8e6)
    # Acción sana con gap pero cierre en la parte baja del rango
    c, v = flat(30, 6e5)
    u["WEAK"] = with_gap(series(c, v), N - 1, 0.15, 0.93, 6e6, low_mult=0.99, high_mult=1.10)
    # Ruido
    for i in range(20):
        c, v = flat(RNG.uniform(5, 100), RNG.uniform(3e5, 5e6))
        u[f"RND{i}"] = series(c, v)
    # Índices en tendencia
    for s in cfg.MARKET["indices"]:
        c = np.linspace(400, 520, N) * np.exp(RNG.normal(0, 0.003, N))
        u[s] = series(c, np.full(N, 5e7))
    return u


class FakeFetcher:
    def __init__(self, prices, cut=None):
        self.prices = prices
        self.cut = cut

    def download(self, tickers, period=None):
        out = {t: self.prices[t] for t in tickers if t in self.prices}
        if self.cut is not None:
            out = {t: df.loc[:self.cut] for t, df in out.items()}
        return out

    def fundamentals(self, ticker, event_date, window_days):
        if ticker == "NEGL":
            return dict(name="Neglected Inc", sector="Technology", mkt_cap=8e8, earnings_catalyst=True,
                        eps_surprise_pct=45.0, rev_yoy_q0=0.85, rev_yoy_q1=0.40)
        return {}


def scan_all(u, asof=DATES[-1]):
    out = dict(ep=[], nine_m=[], delayed=[], followup=[], rejected=[])
    for t, df in u.items():
        r = detect.scan_ticker(t, df, asof, cfg)
        for k in out:
            out[k] += r[k]
    return out


# ── tests ───────────────────────────────────────────────────────────────────

def test_detection():
    u = build_universe()
    r = scan_all(u)
    eps = {x["ticker"]: x for x in r["ep"]}
    assert set(eps) == {"NEGL", "EXTD"}, eps.keys()
    rej = {x["ticker"]: x["reasons"] for x in r["rejected"]}
    assert set(rej) == {"BUST", "PUMP", "WEAK"}, rej
    assert "destruida" in rej["BUST"] and "Cierre débil" in rej["BUST"]
    assert "Pump previo" in rej["PUMP"]
    assert rej["WEAK"].startswith("Cierre débil")
    assert eps["NEGL"]["neglected"] is True
    assert eps["EXTD"]["neglected"] is False
    assert "FADE" not in eps
    assert [x["ticker"] for x in r["nine_m"]] == ["NINE"]
    dl = {x["ticker"]: x["kind"] for x in r["delayed"]}
    assert dl == {"DELB": "Breakout", "DELS": "Setup"}, dl
    fu = {x["ticker"] for x in r["followup"]}
    assert {"NEGL", "EXTD", "DELB", "DELS", "FILL"} <= fu
    assert not {"BUST", "PUMP", "WEAK"} & fu
    fill = [x for x in r["followup"] if x["ticker"] == "FILL"][0]
    assert fill["gap_filled"] and fill["status"] == "Perdió LOD"


def test_score_orders_quality():
    from epscan.score import score
    u = build_universe()
    r = scan_all(u)
    eps = {x["ticker"]: x for x in r["ep"]}
    base_negl, base_ext = score(eps["NEGL"]), score(eps["EXTD"])
    assert base_negl > base_ext
    full = score({**eps["NEGL"], "earnings_catalyst": True, "eps_surprise_pct": 40, "rev_yoy_q0": 0.8})
    assert full >= base_negl + 20 and full <= 100


def test_tracker_evaluate():
    idx = pd.bdate_range("2026-01-05", periods=30)
    base = pd.DataFrame({"Open": 10.0, "High": 10.2, "Low": 9.9, "Close": 10.0, "Volume": 1e6}, index=idx)
    sig = dict(signal_date=idx[0].date().isoformat(), stop=9.5)
    # a) sube: sale por tiempo con R positivo
    up = base.copy()
    up["Close"] = np.linspace(10, 12, 30); up["Open"] = up["Close"].shift(1).fillna(10)
    up["High"] = up[["Open", "Close"]].max(axis=1) * 1.005; up["Low"] = up[["Open", "Close"]].min(axis=1) * 0.998
    e = tracker.evaluate(sig, up, 20, [5, 10, 20])
    assert e["status"] == "closed" and e["r"] > 0 and e["exit_reason"].startswith("tiempo"), e
    # b) toca el stop en la rueda 3
    dn = base.copy(); dn.iloc[3, dn.columns.get_loc("Low")] = 9.4
    e = tracker.evaluate(sig, dn, 20, [5])
    assert e["status"] == "closed" and abs(e["r"] + 1) < 1e-9 and e["exit_reason"] == "stop", e
    # c) gap bajo el stop: sale en la apertura, pierde más de 1R
    gp = base.copy(); gp.iloc[4, gp.columns.get_loc("Open")] = 9.0; gp.iloc[4, gp.columns.get_loc("Low")] = 8.9
    e = tracker.evaluate(sig, gp, 20, [5])
    assert e["exit_reason"] == "gap bajo stop" and e["r"] < -1, e
    # d) abre debajo del stop al día siguiente: inválida
    inv = base.copy(); inv.iloc[1, inv.columns.get_loc("Open")] = 9.4
    assert tracker.evaluate(sig, inv, 20, [5])["status"] == "invalid"
    # e) sin rueda posterior: pendiente
    assert tracker.evaluate(dict(signal_date=idx[-1].date().isoformat(), stop=9.5), base, 20, [5])["status"] == "pending"


AFTER_CLOSE = lambda d: pd.Timestamp(d).tz_localize("America/New_York") + pd.Timedelta(hours=18)
INTRADAY = lambda d: pd.Timestamp(d).tz_localize("America/New_York") + pd.Timedelta(hours=13, minutes=20)


def test_neglect_is_symmetric():
    u = build_universe()
    r = scan_all(u)
    eps = {x["ticker"]: x for x in r["ep"]}
    from epscan.score import score
    crashed = {**eps["NEGL"], "ret_3m": -0.80}
    assert detect.neglect_at is not None
    assert score(crashed) < score(eps["NEGL"]) - 15


def test_end_to_end():
    u = build_universe()
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "data").mkdir()
        tickers = [t for t in u if t not in cfg.MARKET["indices"]]
        pd.DataFrame({"ticker": tickers, "name": "", "exchange": "NYSE", "built": "2026-09-29"}) \
            .to_csv(tmp / "data" / "universe.csv", index=False)

        # Corrida 1: 30 ruedas atrás (genera señales que después se evalúan)
        past = DATES[-41]
        ctx = main.run(cfg, FakeFetcher(u, cut=past), root=tmp, today=past.date(), now=AFTER_CLOSE(past))
        assert ctx is not None
        # Corrida intradiaria de hoy: reporte provisional, sin registrar señales ni marcar el cierre
        before = pd.read_csv(tmp / "data" / "signals.csv")
        ctx = main.run(cfg, FakeFetcher(u), root=tmp, today=DATES[-1].date(), now=INTRADAY(DATES[-1]))
        assert ctx["provisional"] and len(pd.read_csv(tmp / "data" / "signals.csv")) == len(before)
        assert "Reporte provisional" in (tmp / "docs" / "index.html").read_text()
        # Corrida 2: hoy, después del cierre (no la bloquea la corrida provisional)
        ctx = main.run(cfg, FakeFetcher(u), root=tmp, today=DATES[-1].date(), now=AFTER_CLOSE(DATES[-1]))
        assert not ctx["provisional"]
        assert ctx is not None
        assert {r["ticker"] for r in ctx["ep"]} == {"NEGL", "EXTD"}
        negl = [r for r in ctx["ep"] if r["ticker"] == "NEGL"][0]
        assert negl["earnings_catalyst"] and negl["name"] == "Neglected Inc"
        assert ctx["ep"][0]["ticker"] == "NEGL"  # mayor score
        # Repetir el mismo cierre no reprocesa
        assert main.run(cfg, FakeFetcher(u), root=tmp, today=DATES[-1].date(), now=AFTER_CLOSE(DATES[-1])) is None
        # --force sobre el mismo cierre reemplaza las pendientes del día, no las duplica
        n1 = len(pd.read_csv(tmp / "data" / "signals.csv"))
        main.run(cfg, FakeFetcher(u), root=tmp, today=DATES[-1].date(), now=AFTER_CLOSE(DATES[-1]), force=True)
        assert len(pd.read_csv(tmp / "data" / "signals.csv")) == n1
        # Un estado viejo (formato anterior, marcado por una corrida intradiaria) no bloquea
        (tmp / "data" / "state.json").write_text('{"last_asof": "%s"}' % DATES[-1].date())
        assert main.run(cfg, FakeFetcher(u), root=tmp, today=DATES[-1].date(), now=AFTER_CLOSE(DATES[-1])) is not None
        assert "Descartados por los filtros" in (tmp / "docs" / "index.html").read_text()
        html = (tmp / "docs" / "index.html").read_text()
        for s in ("NEGL", "DELB", "DELS", "9 Million", "Favorable", "BUST", "Pump previo"):
            assert s in html, s
        sig = pd.read_csv(tmp / "data" / "signals.csv")
        assert {"EP", "DEP", "9M"} <= set(sig["type"])
        old = sig[(sig["ticker"] == "OLD") & (sig["type"] == "EP")].iloc[0]
        assert old["status"] == "closed" and old["r"] > 0, old.to_dict()
        assert ctx["stats_type"].loc["EP", "n"] == 1
        for f in ("ep_today", "nine_m", "delayed", "followup", "signals", "stats"):
            assert (tmp / "docs" / "data" / f"{f}.csv").exists()
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
