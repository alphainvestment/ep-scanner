# Scanner de Episodic Pivots

Scanner diario, post-cierre, que recorre ~3.000–4.000 acciones US y publica un reporte HTML con:

| Sección | Qué detecta |
|---|---|
| **EPs del día** | Gap ≥ 8% con RVol ≥ 3x y ≥ USD 10M operados, que no devolvió el gap entero |
| **9 Million EPs** | ≥ 9M acciones, suba ≥ 4%, RVol ≥ 2x (Stockbee), sin ser EP clásico |
| **Delayed EPs** | EPs de hace 3–30 ruedas que nunca cerraron bajo el cierre previo al gap: *Breakout* (hoy rompió la consolidación) o *Setup* (consolidación ajustada cerca de máximos) |
| **Seguimiento** | Todos los EPs de las últimas 20 ruedas con su estado (sobre MM10, perdió el LOD, etc.) |
| **Resultados** | Registro de cada señal y su resultado simulado en R, por tipo y por contexto de mercado |

Cada señal lleva métricas de *neglect* (suba de 3 meses previa, distancia a MM50), fundamentals de Yahoo (resultados en la fecha, sorpresa de EPS, crecimiento de ventas y aceleración) y un score 0–100 para ordenar.

## Estructura

```
config.py               ← todos los parámetros (umbrales, universo, horizonte del tracking)
run.py                  ← punto de entrada
epscan/
  universe.py           listado Nasdaq Trader + filtro de liquidez (se rearma cada 7 días)
  data.py               descarga yfinance por bloques con reintentos + fundamentals
  detect.py             EP, 9M, delayed EP, seguimiento, contexto de mercado
  score.py              score 0-100
  tracker.py            registro de señales y estadísticas en R
  report.py             HTML + CSV
data/                   universe.csv, signals.csv (histórico), state.json
docs/                   index.html + docs/data/*.csv  (lo que publica GitHub Pages)
tests/                  tests offline con series sintéticas
.github/workflows/      corrida automática lunes a viernes 19:30 ART
```

## Puesta en marcha en GitHub

1. Crear un repo nuevo (por ejemplo `ep-scanner`) y subir esta carpeta tal cual.
2. **Settings → Actions → General → Workflow permissions**: *Read and write permissions* (el workflow commitea los resultados).
3. **Settings → Pages**: *Deploy from a branch* → `main` / `/docs`.
4. Prueba rápida: **Actions → Scanner EP diario → Run workflow**, con `NVDA,PLTR,HOOD` en el campo *tickers*. Tarda 1–2 minutos y confirma que Yahoo responde desde GitHub.
5. Primera corrida completa: **Run workflow** con el campo *tickers* vacío. La primera corrida completa tarda más que las siguientes porque arma el universo (descarga ~6.000 símbolos para filtrar liquidez).

**Público o privado.** GitHub Pages en repos privados requiere plan pago. Con el repo privado, el workflow corre igual (~15 min por día, dentro de los 2.000 min gratis) y el reporte se ve bajando el repo y abriendo `docs/index.html`. Con el repo público, la URL de Pages es visible para cualquiera que la tenga.

## Opcional: correr en una PC (Windows)

```powershell
cd ep-scanner
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python run.py --tickers NVDA,PLTR,HOOD   # prueba rápida, no toca el registro
python run.py                            # corrida completa
python run.py --force                    # reprocesa el mismo cierre
python run.py --no-fundamentals          # más rápido, sin consultas de fundamentals
python run.py --rebuild-universe         # rearma el universo ya
```

Para automatizarlo sin GitHub: Programador de tareas de Windows, lunes a viernes 19:30, acción `C:\...\ep-scanner\.venv\Scripts\python.exe run.py` con "Iniciar en" la carpeta del proyecto.

## Google Sheets

Con el repo público y Pages activo, en cualquier hoja:

```
=IMPORTDATA("https://<usuario>.github.io/ep-scanner/data/ep_today.csv")
=IMPORTDATA("https://<usuario>.github.io/ep-scanner/data/signals.csv")
```

También están `nine_m.csv`, `delayed.csv`, `followup.csv` y `stats.csv`.

## Cómo se simulan los resultados

- Entrada: apertura de la rueda siguiente a la señal (el scanner corre al cierre).
- Stop: LOD del día de la señal (EP y 9M) o mínimo de la consolidación (delayed EP). Si la apertura de entrada ya está debajo del stop, la señal queda *invalid*.
- Salida: stop (o la apertura si gapea por debajo) o cierre de la rueda 20.
- Sin comisiones ni slippage. Si una rueda toca el stop, se asume que el stop se ejecutó antes que el máximo.

Esto **no** replica el trade de día 1 con entrada en el ORH: sirve para comparar tipos de señal y contextos de mercado entre sí con tus propios datos. Con menos de 30 señales cerradas por grupo, el reporte lo marca como muestra chica.

## Limitaciones conocidas

- **Yahoo no es una fuente garantizada.** Puede cortar o limitar pedidos desde las IPs de GitHub. El código reintenta por bloques más chicos; si falla seguido, bajar `chunk_size` o subir `pause_s` en `config.py`, o correrlo desde la PC.
- **Fundamentals incompletos.** Fechas de resultados, sorpresa y ventas trimestrales a veces faltan. Un score alto sin datos de resultados requiere revisar el catalizador a mano.
- **Sesgo de supervivencia.** El universo se arma con lo que cotiza hoy.
- **Premarket.** No está cubierto (decisión de alcance). Para el día 1 en vivo, el complemento natural es un scan en thinkorswim.

## Tests

```
python tests/test_scanner.py
```

Arman series sintéticas con cada patrón (EP olvidado, EP extendido, gap devuelto, 9M, delayed breakout y setup, gap cubierto) y verifican la detección, el score, la simulación en R y la corrida completa.
