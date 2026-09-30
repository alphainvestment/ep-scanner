"""Orquestación: universo → precios → detección → fundamentals → registro → reporte."""
from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd

from . import detect, report, tracker, universe
from .score import score

log = logging.getLogger("epscan")
ROOT = Path(__file__).resolve().parent.parent


def run(cfg, fetcher, root: Path = ROOT, force: bool = False, fundamentals: bool = True,
        tickers: list[str] | None = None, today: date | None = None) -> dict | None:
    data_dir, docs_dir = root / "data", root / "docs"
    data_dir.mkdir(exist_ok=True)
    state_path = data_dir / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    # 1) Universo
    if tickers:
        uni = pd.DataFrame({"ticker": tickers, "name": "", "exchange": ""})
    else:
        uni = universe.get_universe(cfg.UNIVERSE, fetcher, data_dir, today=today)
    names = dict(zip(uni["ticker"], uni["name"].fillna("")))

    # 2) Precios (universo + índices + tickers con señales abiertas)
    sig_path = data_dir / "signals.csv"
    signals = tracker.load(sig_path)
    open_tk = signals.loc[~signals["status"].isin(["closed", "invalid"]), "ticker"].dropna().tolist()
    wanted = list(dict.fromkeys(uni["ticker"].tolist() + cfg.MARKET["indices"] + open_tk))
    prices = fetcher.download(wanted, cfg.DOWNLOAD["period"])
    if not prices:
        raise RuntimeError("No se descargaron precios")

    # Fecha de cierre: la más frecuente entre las últimas barras
    asof = Counter(df.index[-1] for df in prices.values()).most_common(1)[0][0]
    asof_s = asof.date().isoformat()
    if state.get("last_asof") == asof_s and not force:
        log.info("Ya se procesó el cierre %s (feriado o corrida repetida). Uso --force para rehacer.", asof_s)
        return None
    log.info("Procesando cierre %s", asof_s)

    # 3) Contexto y detección
    regime = detect.market_regime(prices, cfg.MARKET["indices"])
    ep, nine_m, delayed, followup = [], [], [], []
    for t in uni["ticker"]:
        df = prices.get(t)
        if df is None:
            continue
        try:
            r = detect.scan_ticker(t, df, asof, cfg)
        except Exception as e:  # un ticker con datos raros no frena la corrida
            log.debug("%s: %s", t, e)
            continue
        ep += r["ep"]; nine_m += r["nine_m"]; delayed += r["delayed"]; followup += r["followup"]
    log.info("EPs: %d · 9M: %d · Delayed: %d · Seguimiento: %d", len(ep), len(nine_m), len(delayed), len(followup))

    # 4) Fundamentals para los candidatos (prioridad: EP > delayed breakout > 9M > setup)
    if fundamentals and cfg.FUNDAMENTALS["enabled"] and hasattr(fetcher, "fundamentals"):
        pri = ep + [d for d in delayed if d["kind"] == "Breakout"] + nine_m + \
            [d for d in delayed if d["kind"] == "Setup"]
        cache: dict[str, dict] = {}
        for row in pri[: cfg.FUNDAMENTALS["max_tickers"]]:
            ev_date = pd.Timestamp(row.get("ep_date") or row["date"])
            key = f'{row["ticker"]}|{ev_date.date()}'
            if key not in cache:
                try:
                    cache[key] = fetcher.fundamentals(row["ticker"], ev_date, cfg.FUNDAMENTALS["earnings_window_days"])
                except Exception as e:
                    log.debug("fundamentals %s: %s", row["ticker"], e)
                    cache[key] = {}
            f = cache[key]
            row.update({k: v for k, v in f.items() if k != "name" or not names.get(row["ticker"])})

    for rows in (ep, nine_m, followup):
        for r in rows:
            r.setdefault("name", names.get(r["ticker"], ""))
    for r in ep + nine_m:
        r["score"] = score(r)
    for r in delayed:
        r.setdefault("name", names.get(r["ticker"], ""))
        r["score"] = score({**r, "gap": r["ep_gap"], "rvol": r["ep_rvol"], "close_pos": r["ep_close_pos"],
                            "chg": None})
    ep.sort(key=lambda r: -r["score"]); nine_m.sort(key=lambda r: -r["score"])
    delayed.sort(key=lambda r: (r["kind"] != "Breakout", -r["score"]))
    followup.sort(key=lambda r: (r["days"], -(r["ret_since"] or 0)))

    # 5) Registro de señales y estadísticas
    new = [dict(signal_date=asof_s, ticker=r["ticker"], type="EP", score=r["score"],
                regime=regime["label"], ref_close=r["close"], stop=r["stop_ref"]) for r in ep]
    new += [dict(signal_date=asof_s, ticker=r["ticker"], type="9M", score=r["score"],
                 regime=regime["label"], ref_close=r["close"], stop=r["stop_ref"]) for r in nine_m]
    new += [dict(signal_date=asof_s, ticker=r["ticker"], type="DEP", score=r["score"],
                 regime=regime["label"], ref_close=r["close"], stop=r["stop_ref"])
            for r in delayed if r["kind"] == "Breakout"]
    record = not tickers  # las pruebas con --tickers no ensucian el registro
    if record:
        signals = tracker.append_signals(signals, new)
    signals = tracker.update(signals, prices, cfg.TRACKING["horizon"], cfg.TRACKING["report_horizons"])
    if record:
        signals.to_csv(sig_path, index=False)
    st_type, st_regime = tracker.stats(signals, cfg.TRACKING["report_horizons"])
    counts = signals["status"].value_counts()

    ctx = dict(
        cfg=cfg, asof=asof_s, universe_n=len(uni), regime=regime,
        ep=ep, nine_m=nine_m, delayed=delayed, followup=followup,
        signals=signals, stats_type=st_type, stats_regime=st_regime,
        tracking=dict(total=len(signals), closed=int(counts.get("closed", 0)), open=int(counts.get("open", 0)),
                      pending=int(counts.get("pending", 0)), invalid=int(counts.get("invalid", 0))),
    )
    report.write(docs_dir, ctx)
    if record:
        state.update(last_asof=asof_s)
        state_path.write_text(json.dumps(state, indent=2))
    log.info("Reporte escrito en %s", docs_dir / "index.html")
    return ctx


def cli():
    ap = argparse.ArgumentParser(description="Scanner diario de Episodic Pivots")
    ap.add_argument("--force", action="store_true", help="reprocesa aunque el cierre ya se haya procesado")
    ap.add_argument("--no-fundamentals", action="store_true", help="saltea las consultas de fundamentals")
    ap.add_argument("--tickers", help="lista separada por comas para una prueba rápida (ignora el universo)")
    ap.add_argument("--rebuild-universe", action="store_true", help="fuerza la reconstrucción del universo")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    import config as cfg
    from .data import YahooFetcher
    fetcher = YahooFetcher(cfg.DOWNLOAD)
    if a.rebuild_universe:
        universe.get_universe(cfg.UNIVERSE, fetcher, ROOT / "data", force=True)
    tk = [t.strip().upper() for t in a.tickers.split(",")] if a.tickers else None
    run(cfg, fetcher, force=a.force or bool(tk), fundamentals=not a.no_fundamentals, tickers=tk)
