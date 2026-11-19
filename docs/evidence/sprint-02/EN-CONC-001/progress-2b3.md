# Evidencia reproducible de progreso distribuido

Usar Python del proyecto en WSL2 y binarios PostgreSQL locales. Las fixtures arrancan un clúster desechable de loopback y generan los tres roles, pedidos y materiales sintéticos. Dos procesos Uvicorn atienden sesiones HTTP independientes y el worker ejecuta los motores reales.

```bash
export PYTHON_DOTENV_DISABLED=1
export NEWGLASS_REQUIRE_POSTGRES=1
export NEWGLASS_TEST_PG_BIN="$(pg_config --bindir)"
python -m pytest tests/integration/optimization/test_distributed_progress.py tests/integration/optimization/test_comparison_v2_http_restart.py -q -ra -s
```

El escenario principal imprime `progress_http` con una muestra de seis lecturas HTTP durante un FF activo, tamaño de la página, RSS de procesos, delta de CPU y conexiones PostgreSQL por servicio. La comparación se mantiene activa mediante una barrera de prueba controlada. Esta medición corresponde a una prueba local de baja carga.

Las pruebas comprueban que los hitos reaparecen en otra API, la cancelación se publica únicamente si su transacción confirma, un cursor vencido puede recuperarse y una consulta posterior a una interrupción transitoria conserva el cursor. Se mide el rechazo de saturación y se comprueba la revocación de permisos. Almacenero registra una plancha independiente, aumenta existencias y genera un movimiento manual válido mientras el worker calcula. La baja conflictiva se revierte y la cancelación de otro pedido conserva las referencias aún vigentes. Las pruebas de confirmación utilizan exclusivamente inventario sintético y comprueban que una unidad se consume una sola vez.

La regresión de FF corta PostgreSQL justo antes de persistir el hito de inicio. El intento vigente reintenta la escritura con un límite breve. El resultado de cancelación y el cierre se publican una vez, y V2 solo reclama la plaza después del cierre acreditado.
