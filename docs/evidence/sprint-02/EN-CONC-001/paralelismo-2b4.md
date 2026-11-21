# Evidencia de paralelismo controlado de 2B.4

La prueba se ejecutó en WSL2 con PostgreSQL temporal, dos procesos Uvicorn y dos workers Linux. Ambos workers estaban en una slice con `MemoryMax=1700M`, swap cero, `CPUQuota=200%` y 64 tareas máximas. La máquina tenía 7.620 MiB de RAM visibles. Las pruebas generaron planchas sintéticas de 600 × 450 mm y pedidos con dos tamaños rectangulares y triángulos convexos. El raster de 5 mm y el paso angular de 5° se conservaron en ambas capacidades. [Reproducción del laboratorio](../../../specs/ADR-EN-CONC-001-paralelismo-controlado.md)

## Solapamiento del motor real

La instrumentación midió la llamada original a `FirstFitOptimizer.optimize` en los dos procesos hijos con 20 piezas. El instante cero corresponde al inicio del primer motor. La barrera de coordinación y las esperas de la prueba quedan fuera de la medición.

| Motor | PID | Inicio relativo | Fin relativo | CPU del hilo | Cobertura |
| --- | ---: | ---: | ---: | ---: | ---: |
| FF para Administrador | 1618 | 0,000 ms | 18,223 ms | 14,504 ms | 20/20 |
| FF para Operario | 1611 | 0,090 ms | 12,724 ms | 12,631 ms | 20/20 |

El solapamiento fue de 12,635 ms. La CPU total de ambas llamadas fue de 27,135 ms en una ventana de 18,223 ms. Los propietarios se encontraban en las plazas 1 y 2. Tras reiniciar ambas APIs, la cancelación de un trabajo acreditó el cierre en 271 ms y el tercero reclamó una plaza después de ese cierre. El otro trabajo siguió activo. El trabajo superviviente y el tercero completaron los seis motores.

## Medición de carga

Cada fila proviene de un par de comparaciones sobre dos pedidos del mismo tamaño. La pared se midió desde el envío de las solicitudes hasta el cierre de ambos trabajos. Cada percentil usa 120 consultas de progreso y 120 lecturas de inventario. Las últimas columnas expresan p50/p95/p99 en milisegundos.

| Piezas por pedido | Plazas | Pared del par (s) | Espera A / O (ms) | Cálculo A / O (s) | CPU worker A / O (ms) | Progreso (ms) | Inventario (ms) |
| ---: | ---: | ---: | --- | --- | --- | --- | --- |
| 20 | 1 | 6,550 | 3.395 / 273 | 2,948 / 2,832 | 2.970 / 2.821 | 10,5 / 13,8 / 15,9 | 7,3 / 11,8 / 13,9 |
| 20 | 2 | 3,882 | 503 / 271 | 3,189 / 3,169 | 3.070 / 3.087 | 11,6 / 15,6 / 17,1 | 7,8 / 13,2 / 14,9 |
| 40 | 1 | 8,029 | 4.132 / 270 | 3,667 / 3,702 | 3.909 / 4.061 | 11,9 / 15,7 / 19,6 | 8,0 / 13,7 / 17,3 |
| 40 | 2 | 5,032 | 485 / 237 | 4,268 / 4,006 | 4.174 / 3.953 | 11,3 / 14,6 / 36,5 | 7,7 / 12,0 / 14,3 |
| 100 | 1 | 13,758 | 7.113 / 248 | 6,303 / 6,628 | 7.193 / 7.675 | 12,3 / 15,7 / 16,9 | 8,3 / 13,5 / 14,2 |
| 100 | 2 | 8,725 | 252 / 516 | 7,937 / 7,886 | 7.871 / 7.879 | 12,3 / 15,9 / 18,6 | 8,3 / 13,1 / 15,1 |

Las doce comparaciones de la tabla terminaron completas, con 72 resultados de motor completos y cero movimientos de simulación. Se observaron hasta cuatro conexiones de workers y tres de APIs. El máximo de memoria contabilizada por el cgroup fue de 218,3 MiB en carga normal. El pico de RSS agregado muestreado fue de 344,4 MiB, con hasta 99,0 MiB por proceso. La suma de RSS cuenta páginas compartidas y `memory.peak` mide la contabilidad del kernel. El mínimo de `MemAvailable` fue de unos 6.131 MiB. El cgroup alcanzó 15 tareas, siete procesos y 3,59 s acumulados de throttling en el caso de 100 piezas con dos plazas. En ese caso el máximo PSI de CPU fue de 7,64 % en el host y 6,79 % en el cgroup. Los casos de carga registraron cero eventos OOM.

## Integridad y recuperación

- Dos instancias API rechazaron el acceso ajeno y el reclamo incompatible del mismo pedido. La cancelación de Administrador mantuvo activo el trabajo de Operario. Almacenero creó una plancha independiente en 25,7 ms, actualizó sus existencias en 7,7 ms y consultó movimientos. La modificación incompatible de la plancha compartida devolvió conflicto. Las simulaciones conservaron las existencias.
- Con dos plazas habilitadas, un fallo SQL forzado al guardar el recibo revirtió el consumo y los movimientos. Al reintentar y disputar una unidad sintética desde dos APIs, una confirmación devolvió `200` y la otra `409`. Repetir el recibo ganador conservó un solo movimiento y la cantidad final de la plancha fue cero.
- Una prueba detuvo un supervisor con `SIGSTOP`, venció su heartbeat y comprobó el rechazo del propietario obsoleto. Tras la caída del proceso, su plaza conservó `RECOVERY_BLOCKED`. El otro worker reclamó un nuevo trabajo en la otra plaza a los 2,85 s. La plaza bloqueada quedó pendiente de cierre verificable.
- Se interrumpió PostgreSQL para uno de dos workers activos. Pasó a terminal con su generación original tras recuperar conectividad. El otro conservó su propietario y completó los seis motores en 2,90 s desde la restitución de la conexión. El inventario conservó sus cantidades y movimientos.
- La prueba de presión alcanzó 1.443 MiB contabilizados de un límite de 1.700 MiB. Ambos reclamantes dejaron el trabajo en espera y sin intento registrado. Tras retirar la presión, el trabajo cerró en 2,42 s. Se observaron cero OOM y cero procesos terminados por OOM. Una prueba unitaria verifica que un contador OOM distinto impide continuar con la acreditación anterior.

El ensayo acredita este host y estas geometrías hasta 100 piezas. La capacidad predeterminada sigue siendo uno. Dos plazas siguen limitadas al laboratorio que comparte la misma slice acreditada.
