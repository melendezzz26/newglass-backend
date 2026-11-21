# Progreso de optimización entre instancias API

## Recursos

- `GET /api/optimization/comparisons-v2/{id}/progress` para V2.
- `GET /api/optimization/runs/durable/{id}/progress` para FF durable.

Ambas rutas requieren `optimization.view` y propiedad del trabajo. Devuelven `404` a un usuario ajeno, incluso si es Administrador. V1 conserva sus rutas existentes. Las consultas de estado, resultado, cancelación y confirmación mantienen sus contratos. El stream visual existente se mantiene en su modo soportado y responde `410 VISUAL_DATA_UNAVAILABLE` en el piloto separado.

## Paginación y reconexión

Sin `after_sequence`, la API devuelve los hitos que aún conserva, empezando por el más antiguo. El parámetro `limit` acepta de 1 a 32 eventos y por defecto vale 16. Cada respuesta contiene `job_id`, `profile`, `version`, `state`, `result_state`, `cancellation`, `current_engine`, `result_url`, `events`, `next_sequence`, `has_more` y `poll_after_ms`.

El cliente guarda `next_sequence` después de aplicar la página. Si `has_more` es `true`, puede recuperar la siguiente página enseguida. Cuando se pone al día, respeta `poll_after_ms` de 1.000 ms. Puede cambiar de instancia API durante la consulta. Las respuestas no se cachean. Las secuencias solo tienen significado dentro de un trabajo.

Un cursor que quedó atrás devuelve `409 EVENT_CURSOR_EXPIRED` con `resume_after_sequence` y `result_url`. El cliente puede releer `/progress` sin cursor, actualizar el estado desde esa página y recuperar el resultado comercial desde `result_url`. Luego continúa desde el cursor entregado por el servidor. Un cursor futuro devuelve `409 STATE_CONFLICT`. La saturación devuelve `429 PROGRESS_BUSY` y la indisponibilidad temporal de PostgreSQL devuelve `503 RESOURCE_UNAVAILABLE`. Ambos incluyen `Retry-After` de un segundo.

## Estado que muestra el cliente

| Campo | Interpretación |
| --- | --- |
| `state=WAITING` | En espera de la plaza compartida. |
| `state=RUNNING` | Trabajo reclamado. `current_engine` contiene el motor cuando su inicio está acreditado. Puede ser `null` durante el arranque o entre motores. |
| `cancellation=REQUESTED` | La cancelación se guardó y el worker aún debe cerrar sus procesos. |
| `state=TERMINAL` | Cierre acreditado. `result_state` indica si hay una solución elegible, si terminó sin solución o si falló. Si `cancellation=CANCELLED`, mostrar Cancelado. |
| `state=RECOVERY_BLOCKED` | Recuperación bloqueada. Si hay un motor en la respuesta, es el último inicio acreditado. Presentar el resultado cuando el servidor acredite el cierre. |

Los eventos de motor exponen el ID de ejecución, el método, el instante PostgreSQL y el motivo de finalización cuando existe. Un motor cancelado antes de arrancar no se anuncia como iniciado o finalizado. Las páginas contienen hitos y estado, sin geometría, máscaras ni porcentajes. Para obtener colocaciones y resultados, utilizar las rutas comerciales ya existentes.

## Límites y operación

El worker y cada API usan la misma configuración de retención. La API consulta mediante peticiones cortas y comparte la conexión de autenticación con la lectura. El límite de concurrencia y de frecuencia aplica al recurso `/progress` por proceso API. Volver a calcular el presupuesto de conexiones al añadir instancias.

| Variable | Valor inicial | Máximo |
| --- | ---: | ---: |
| `NEWGLASS_PROGRESS_RETAINED_EVENTS` | 64 por trabajo | 256 |
| `NEWGLASS_PROGRESS_TTL_SECONDS` | 3.600 s | 86.400 s |
| `NEWGLASS_PROGRESS_MAX_READERS` | 4 por API | 32 |
| `NEWGLASS_PROGRESS_READS_PER_SECOND` | 24 por API | 200 |

El worker elimina en tandas los eventos vencidos. La lectura aplica el vencimiento aunque el recolector esté detenido. El contador del trabajo permanece para distinguir cursores vencidos. Los trabajos anteriores conservan sus snapshots y empiezan a registrar secuencias con la siguiente transición real. Para actualizar el piloto, aplicar `s2conc01b300` en la ventana de cambio y reiniciar API y worker con el mismo binario. El downgrade de esquema rechaza la pérdida de cursores o protecciones abiertas.
