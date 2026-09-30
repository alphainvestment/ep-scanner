"""Universo de acciones US: listado oficial de Nasdaq Trader + filtro de liquidez semanal."""
from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger("epscan")

NASDAQ_TRADED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"


def fetch_symbol_list(cfg: dict) -> pd.DataFrame:
    r = requests.get(NASDAQ_TRADED_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return parse_symbol_list(r.text, cfg)


def parse_symbol_list(text: str, cfg: dict) -> pd.DataFrame:
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("File Creation Time")]
    df = pd.read_csv(io.StringIO("\n".join(lines)), sep="|", dtype=str)
    df = df[(df["Nasdaq Traded"] == "Y") & (df["ETF"] == "N") & (df["Test Issue"] == "N")]
    df = df[~df["Symbol"].str.contains(r"[\$\^=]", regex=True, na=True)]
    pat = re.compile("|".join(cfg["exclude_name_patterns"]), re.IGNORECASE)
    df = df[~df["Security Name"].fillna("").str.contains(pat)]
    out = pd.DataFrame({
        "ticker": df["Symbol"].str.replace(".", "-", regex=False).str.strip(),
        "name": df["Security Name"].str.strip(),
        "exchange": df["Listing Exchange"].map(
            {"Q": "NASDAQ", "N": "NYSE", "A": "AMEX", "P": "ARCA", "Z": "BATS", "V": "IEX"}
        ).fillna(df["Listing Exchange"]),
    })
    return out.drop_duplicates("ticker").reset_index(drop=True)


def liquidity_filter(prices: dict[str, pd.DataFrame], cfg: dict) -> set[str]:
    keep = set()
    for t, df in prices.items():
        tail = df.tail(20)
        if tail.empty:
            continue
        if tail["Close"].iloc[-1] < cfg["min_price"]:
            continue
        if (tail["Close"] * tail["Volume"]).mean() < cfg["min_avg_dollar_vol"]:
            continue
        keep.add(t)
    return keep


def get_universe(cfg: dict, fetcher, data_dir: Path, today: date | None = None,
                 force: bool = False) -> pd.DataFrame:
    """Devuelve el universo cacheado o lo reconstruye si venció."""
    path = data_dir / "universe.csv"
    today = today or date.today()
    if path.exists() and not force:
        uni = pd.read_csv(path, dtype=str)
        built = datetime.fromisoformat(uni["built"].iloc[0]).date() if len(uni) else date.min
        if (today - built).days < cfg["refresh_days"]:
            log.info("Universo en caché: %d tickers (armado %s)", len(uni), built)
            return _apply_overrides(uni, cfg)

    try:
        raw = fetch_symbol_list(cfg)
        log.info("Listado Nasdaq Trader: %d símbolos candidatos", len(raw))
        prices = fetcher.download(raw["ticker"].tolist(), period="1mo")
        keep = liquidity_filter(prices, cfg)
        uni = raw[raw["ticker"].isin(keep)].copy()
        uni["built"] = today.isoformat()
        uni.to_csv(path, index=False)
        log.info("Universo reconstruido: %d tickers líquidos", len(uni))
    except Exception as e:
        if not path.exists():
            raise RuntimeError(f"No se pudo armar el universo y no hay caché: {e}") from e
        log.warning("Falló la reconstrucción del universo (%s); uso el caché", e)
        uni = pd.read_csv(path, dtype=str)
    return _apply_overrides(uni, cfg)


def _apply_overrides(uni: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    uni = uni[~uni["ticker"].isin(cfg.get("exclude_tickers", []))]
    extra = [t for t in cfg.get("extra_tickers", []) if t not in set(uni["ticker"])]
    if extra:
        uni = pd.concat([uni, pd.DataFrame({"ticker": extra, "name": "", "exchange": ""})])
    return uni.reset_index(drop=True)
