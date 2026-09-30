"""
Parámetros del scanner de Episodic Pivots.
Todo lo que se ajusta a mano está acá. Los porcentajes van en decimales (0.08 = 8%).
"""

# ── Universo ────────────────────────────────────────────────────────────────
# Se reconstruye cada `refresh_days` días desde el listado oficial de Nasdaq Trader
# (NYSE + Nasdaq + NYSE American, sin ETFs). El filtro de liquidez es laxo a propósito:
# muchos EPs salen de acciones olvidadas que antes del gap operaban poco.
UNIVERSE = dict(
    refresh_days=7,
    min_price=2.0,                 # precio mínimo (último cierre)
    min_avg_dollar_vol=1_000_000,  # USD promedio operado en 20 ruedas
    exclude_name_patterns=[        # regex, sin distinguir mayúsculas
        r"\bwarrants?\b", r"\bunits?\b", r"\brights?\b", r"preferred",
        r"\bnotes?\b", r"debentures?", r"acquisition corp", r"\bfund\b",
    ],
    extra_tickers=[],              # tickers a incluir siempre (formato Yahoo: BRK-B)
    exclude_tickers=[],
)

# ── EP del día ──────────────────────────────────────────────────────────────
EP = dict(
    min_gap=0.08,                  # apertura vs cierre previo
    min_rvol=3.0,                  # volumen del día / promedio de 50 ruedas previas
    avg_vol_window=50,
    min_price=2.0,
    min_dollar_vol=10_000_000,     # USD operados el día del gap
    require_hold=True,             # descarta los que cerraron debajo del cierre previo (gap devuelto entero)
)

# ── 9 Million EP (Stockbee) ─────────────────────────────────────────────────
NINE_M = dict(
    min_volume=9_000_000,
    min_change=0.04,               # variación cierre a cierre
    min_rvol=2.0,                  # evita las mega caps que operan 9M todos los días
    min_price=2.0,
)

# ── Filtros de calidad ──────────────────────────────────────────────────────
# Separan un EP de un pump de microcap. Lo que no los pasa va a la lista de
# "Descartados" del reporte con el motivo, para poder auditar los filtros.
QUALITY = dict(
    min_price=2.0,                 # precio de cierre mínimo el día de la señal
    min_close_pos=0.50,            # cierre en la mitad superior del rango del día (sólo día 1)
    max_off_52wh=-0.70,            # descarta acciones destruidas: cierre previo a más de 70% del máx. 52 sem.
    runup_window=10,               # ruedas previas para medir un pump anterior
    max_prior_runup=1.00,          # máx/mín de esas ruedas: más de +100% = ya venía bombeada
)

# ── Neglect (acción olvidada antes del catalizador) ─────────────────────────
# Olvidada = lateral, no desplomada: la variación de 3 meses tiene que estar en [min_ret, max_ret].
NEGLECT = dict(
    lookback=63,                   # ~3 meses de ruedas
    min_ret=-0.30,
    max_ret=0.30,
    max_ext_sma50=0.20,            # distancia máxima a la media de 50 el día previo
)

# ── Horario ─────────────────────────────────────────────────────────────────
# Si el scanner corre antes de esta hora (Nueva York) del mismo día, la barra diaria
# todavía está abierta: genera un reporte PROVISIONAL, sin registrar señales.
SESSION = dict(final_after="16:30", tz="America/New_York")

# ── Delayed EP ──────────────────────────────────────────────────────────────
DELAYED = dict(
    min_days=3,                    # el EP original tiene que tener al menos N ruedas
    max_days=30,                   # y como máximo N ruedas
    max_from_high=0.10,            # cierre actual a no más de 10% del máximo desde el EP
    tight_window=5,                # ruedas para medir la compresión
    max_tight_range=0.12,          # rango (máx-mín)/cierre de esas ruedas para calificar como "setup"
    breakout_window=5,             # breakout = cierre sobre el máximo de las N ruedas previas
)

# ── Seguimiento de EPs recientes ────────────────────────────────────────────
FOLLOWUP = dict(days=20)

# ── Registro de señales y estadísticas ──────────────────────────────────────
# Entrada simulada: apertura de la rueda siguiente a la señal (el scanner corre al cierre).
# Stop: mínimo del día de la señal (EP / 9M) o mínimo de la consolidación (delayed EP).
# Salida: stop tocado, o cierre de la rueda N° `horizon` contando la de entrada.
TRACKING = dict(
    horizon=20,
    report_horizons=[5, 10, 20],
    min_sample_warning=30,
)

# ── Descarga de datos (yfinance) ────────────────────────────────────────────
DOWNLOAD = dict(
    period="1y",
    chunk_size=150,
    pause_s=2.0,                   # pausa entre bloques para no gatillar el rate limit de Yahoo
    retries=2,
)

# ── Fundamentals (gratis, vía yfinance) ─────────────────────────────────────
FUNDAMENTALS = dict(
    enabled=True,
    max_tickers=60,                # tope de consultas por corrida
    earnings_window_days=4,        # resultados publicados hasta N días corridos antes del gap
)

# ── Contexto de mercado ─────────────────────────────────────────────────────
MARKET = dict(indices=["SPY", "QQQ", "IWM"])
