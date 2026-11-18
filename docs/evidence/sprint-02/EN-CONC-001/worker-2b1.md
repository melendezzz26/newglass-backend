# EN-CONC-001 / Evidencia del piloto 2B.1

La prueba TCP levanta dos procesos Uvicorn y dos candidatos al servicio de cálculo. Usa PostgreSQL temporal de loopback, aplica la cadena Alembic y crea datos sintéticos. La primera comparación mantiene vivo su proceso FF mientras se reinicia una API. La segunda sigue en espera hasta que el supervisor acredita el cierre y libera la única plaza. Las dos APIs pueden consultar los seis resultados persistidos de cada comparación.

## Muestra local WSL2

| Comparación sintética | Motores completos | Tiempo de pared (ms) | CPU (ms) | Pico RSS agregado (bytes) |
|---|---:|---:|---:|---:|
| Primera, incluida la pausa para reiniciar la API | 6 | 10.050 | 4.010 | 136.884.224 |
| Segunda | 6 | 4.648 | 3.738 | 136.806.400 |

Durante el primer cálculo se observaron dos conexiones worker totales. El pool admite dos por candidato, sin overflow. Los contadores de inventario permanecieron en una plancha, cero retazos y cero movimientos durante ambas simulaciones. Las pruebas de confirmación consumen exclusivamente la unidad sintética de la base desechable.

La muestra usa una pieza rectangular de 2 × 2 mm sobre una plancha de 5 × 5 mm. El tiempo incluye arranque de procesos, supervisión y persistencia. La primera ejecución también incluye la pausa de la prueba. Estas cifras son una línea base del piloto con una plaza.

## Reproducción

Con el Python existente en WSL2 y los binarios locales de PostgreSQL:

```bash
export PYTHON_DOTENV_DISABLED=1
export NEWGLASS_REQUIRE_POSTGRES=1
export NEWGLASS_TEST_PG_BIN="$(pg_config --bindir)"
python -m pytest -q -s tests/integration/optimization/test_comparison_v2_http_restart.py
```

Las pruebas incluyen caída del reclamante, pérdida temporal de conexión, escritura con token obsoleto, cancelaciones, revocación en espera y adopción transaccional de pendientes 2A. La regresión V3/V4 y el rollback de confirmación se comprueban con las tandas de política residual y cola del backend.
