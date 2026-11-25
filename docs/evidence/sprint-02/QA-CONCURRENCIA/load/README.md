# Arnés de carga con Locust — S2-QA-CONCURRENCIA

Mide la concurrencia de NewGlass con carga HTTP real **solo en loopback**: un PostgreSQL
temporal propio, una API Uvicorn (`--workers 1`, como el `Procfile`) y el worker V2 separado.
Nunca apunta a Heroku, GitHub Pages ni Supabase, y no modifica ningún archivo de las repos:
copia el backend con `git archive` (solo lectura) al disco Linux de WSL2 y agrega este arnés
en esa copia.

## Archivos

| Archivo | Qué hace |
|---|---|
| `run_all.sh` | Punto de entrada. Copia el backend (commit fijo), prepara el venv si falta, corre los escenarios y genera métricas y gráficos. |
| `run_load.py` | Orquestador: clúster PostgreSQL en `127.0.0.1`, Alembic, datos sintéticos, piloto durable, API, worker, Locust por nivel y repetición, muestreo de recursos y verificaciones en PostgreSQL. |
| `locustfile.py` | Escenarios: `AdministradorReader`, `AlmaceneroReader`, `OperarioReader` (E1), `ComparisonUser` (E2) y `ConfirmUser` (E3). |
| `seed_data.py` | Cuentas, pedidos, clientes, planchas y retazos sintéticos. |
| `sampler.py` | CPU y RSS de la API y del worker (con descendientes) cada 200 ms con `psutil`. |
| `analyze.py` | Métricas ISO/IEC 25023, resúmenes CSV/JSON y gráficos PNG con matplotlib. |
| `common.py` | Controles de loopback y nombres de cuentas. |
| `requirements-locust.txt` | Locust, matplotlib y psutil. **No** se agrega al `requirements.txt` de producción. |

## Requisitos

- Windows con WSL2 (Ubuntu 24.04 probado) o Linux. El worker separado y el muestreo usan Linux.
- Binarios de PostgreSQL 16 (`initdb`, `pg_ctl`, `postgres`). `run_all.sh` usa
  `~/newglass-qa/pg/usr/lib/postgresql/16/bin` si existe; si no, `pg_config --bindir`.
- Python 3 con `venv`. Si no existe `~/newglass-qa/conc/.venv`, el script lo crea e instala
  `requirements.txt` del backend más `requirements-locust.txt`.
- El checkout de `newglass-backend` que contiene esta carpeta (se detecta solo) o la
  variable `BACKEND_SRC`.

## Cómo reproducir

Desde PowerShell, en la raíz de `newglass-backend`:

```powershell
wsl -e bash docs/evidence/sprint-02/QA-CONCURRENCIA/load/run_all.sh
```

Toda la batería (E1, E2 y E3) tarda unos 30 minutos; E1R, E1S y E2b, unos 35 más. Para un escenario o un piloto corto:

```powershell
wsl -e bash docs/evidence/sprint-02/QA-CONCURRENCIA/load/run_all.sh --scenarios E2 --levels 2 --reps 1
```

Los resultados quedan en `~/newglass-qa/conc/out/resultados` y los gráficos en
`~/newglass-qa/conc/out/graficos` (dentro de WSL2); se copian a esta carpeta para la evidencia.

Variables opcionales: `BACKEND_REF` (commit medido, por defecto `aa6883f`), `QA_HOME`,
`PG_ROOT`, `VENV`, `OUT` y `QA_E3_REPS` (repeticiones de E3, por defecto 20).

## Seguridad de la prueba

- El orquestador y el `locustfile` abortan si el host o `DATABASE_URL` no son loopback.
- `DATABASE_URL` apunta al clúster temporal con autenticación `trust` local, sin contraseña.
- `SECRET_KEY` y la contraseña sintética de las cuentas se generan en memoria en cada
  ejecución (`secrets.token_urlsafe`) y solo viajan por el entorno de los procesos hijos.
  No se escriben en disco ni en los resultados. `.env` no se lee (`PYTHON_DOTENV_DISABLED=1`).
- Cada usuario Locust inicia sesión una sola vez, en `on_start`, con credenciales válidas:
  el límite de intentos de EN-SEC-001 no se activa.
- Al terminar cada escenario se detienen la API y el worker y se borra el clúster.

## Estructura de `resultados/`

```
environment.json                 entorno exacto (SHA, SO, CPU, RAM, Python, PostgreSQL, Locust)
E1_seed_counts.json ...          volumen de datos sintéticos por escenario
E1/u05_r2/                       una corrida (usuarios 05, repetición 2)
  locust_stats.csv, locust_stats_history.csv, locust_failures.csv, locust_exceptions.csv
  locust_report.html             informe HTML de Locust
  requests.csv                   registro propio por petición (inicio, estado, ms, código)
  resources.csv                  CPU % y RSS MiB de API y worker cada 200 ms
  phases.json, run.json          instantes de inicio, fin del ramp-up y fin; parámetros
  e2_comparisons.jsonl           (E2) admisión, eventos de PostgreSQL y resultado por comparación
  e3_confirmations.jsonl         (E3) cada confirmación con su recibo
  verify.json                    (E2/E3) verificación en PostgreSQL después de la corrida
resumen_E1.csv, resumen_E2.csv, e2_comparaciones.csv, resumen_E3.json
metricas_iso25023.csv/.json      tabla de métricas con valor, umbral y veredicto
```
