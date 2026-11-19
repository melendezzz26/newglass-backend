# EN-CONC-001 / Evidencia de FF durable

## Reproducción

Desde el backend en Linux o WSL2, reutilizar el intérprete del proyecto y señalar los binarios PostgreSQL locales. La fixture inicia su clúster temporal y asigna una base aislada por escenario. Las APIs y candidatos se ejecutan en procesos independientes. Las barreras de prueba retienen únicamente solicitudes sintéticas dentro del supervisor real. Las posiciones las calcula el motor FF real.

```bash
export PYTHON_DOTENV_DISABLED=1
export NEWGLASS_REQUIRE_POSTGRES=1
export NEWGLASS_TEST_PG_BIN="$(pg_config --bindir)"
python -m pytest tests/integration/optimization/test_comparison_v2_http_restart.py -q -ra -s
```

## Comprobaciones automatizadas

| Escenario | Evidencia en la prueba |
| --- | --- |
| FF primero | Dos APIs y dos candidatos, reintento por la misma clave, reinicio de las dos APIs durante FF, V2 en espera y seis resultados V2 posteriores al join de FF. |
| V2 o V1 primero | FF permanece en espera. Tras V2, el Operario obtiene su FF. Si se revoca el permiso mientras espera tras V1, se libera únicamente el pedido sin lanzar un intento. |
| Idempotencia y roles | Mismo ID en reintentos, rechazo de lote distinto con la misma clave, mismo pedido rechazado entre propietarios, privacidad en ambas direcciones, acceso de Almacenero a inventario y rechazo de ejecución y confirmación. |
| Capacidad | Dos solicitudes concurrentes FF/V2 disputan el último lugar de espera. El rechazo no reserva el pedido y reintentar la solicitud aceptada recupera su ID. |
| Fencing y recuperación | Caducidad con propietario vivo, reaparición sin permisos de escritura y muerte previa a despacho con reintento en generación 2. Una muerte después de despacho sin join durable mantiene el bloqueo y la reserva. Una muerte después de publicar preserva el resultado original. |
| Conectividad e inventario | Interrupción real de la conexión del worker. El cierre acreditado precede al relevo. La simulación conserva las existencias y movimientos. La confirmación concurrente FF/V2 sobre stock sintético produce un único consumo y el fallo transaccional revierte todo. |

El escenario que reinicia ambas APIs imprime `worker_ff_returned` con tiempo de pared, CPU, pico RSS agregado muestreado y límite del pool. El tiempo de pared incluye la espera introducida por la prueba. La métrica de conexiones corresponde al límite configurado por candidato.
