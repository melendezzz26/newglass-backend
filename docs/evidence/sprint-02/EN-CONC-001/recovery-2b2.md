# EN-CONC-001 / Evidencia de recuperación y exclusión 2B.2

Las pruebas TCP crean un clúster PostgreSQL descartable, aplican la cadena Alembic y usan datos sintéticos. Dos procesos Uvicorn y dos candidatos al worker comparten la base. Las dos APIs se reinician mientras el PID FF sigue vivo. Ambos trabajos terminan y sus resultados V4 se leen desde las dos instancias, con una sola plaza y sin consumo durante la simulación.

| Comparación | Motores completos | Tiempo de pared (ms) | CPU (ms) | Pico RSS agregado (bytes) |
|---|---:|---:|---:|---:|
| Primera, incluye la pausa por reinicio de las APIs | 6 | 8.689 | 2.549 | 136.548.352 |
| Segunda | 6 | 2.569 | 2.229 | 136.417.280 |

Es una muestra WSL2 de dos comparaciones sucesivas, cada una con una pieza de 2 × 2 mm sobre plancha de 5 × 5 mm. Se registraron dos conexiones de worker durante el primer cálculo. La prueba verifica que ninguna esté inactiva dentro de una transacción durante más de un segundo. La muestra no acredita un presupuesto para dos plazas.

| Caso | Evidencia de seguridad |
|---|---|
| Heartbeat vencido con propietario vivo | Un proceso real suspendido conserva la plaza, aunque vence el heartbeat. Reanudarlo no le devuelve autoridad. |
| Caída antes de despacho | Después de acreditar la salida del propietario real y comprobar que no hubo intención de spawn, se acepta la generación 2 del mismo trabajo. Los intentos con token o generación anteriores quedan rechazados. |
| Caída con FF guardado | Se conserva el resultado V4 y su rectángulo recuperable. El join acreditado permite cerrar los motores sin despacho como interrumpidos. |
| Caída durante un motor | El intento, la reserva y la plaza quedan bloqueados ante falta de evidencia. La API conserva la intención de cancelación. |
| V1 y V2 | V2 permanece en espera durante V1. V1 recibe `503` durante V2. Una repetición por otra API recupera la misma comparación V1. La cancelación en BF conserva el FF previo. |
| Compatibilidad y stock | Se importan filas 2B.1 sin reescribir sus snapshots. La regresión de V3/V4 verifica idempotencia de confirmación, un solo consumo sintético y rollback. |

```bash
export PYTHON_DOTENV_DISABLED=1
export NEWGLASS_REQUIRE_POSTGRES=1
export NEWGLASS_TEST_PG_BIN="$(pg_config --bindir)"
python -m pytest -q -s tests/integration/optimization/test_comparison_v2_http_restart.py
```

La evidencia de recuperación del cierre entre procesos usa únicamente la identificación y el join registrados, al igual que en el servicio. Los arneses solo controlan cuándo detener el propietario o pausar un motor. FF independiente permanece fuera del piloto mixto.
