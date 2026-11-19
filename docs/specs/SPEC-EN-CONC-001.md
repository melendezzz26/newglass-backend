# EN-CONC-001 / Fase 2A — Cola multiusuario

La admisión V2 reserva pedidos y congela el problema en una sola transacción. La comparación y sus seis ejecuciones conservan sus identidades actuales. Una marca `queue_version=1` distingue una solicitud admitida de un registro histórico cuyo dueño se desconoce. PostgreSQL serializa el cupo de espera por transacción y el reclamo del supervisor. La clave de idempotencia sigue siendo única por actor y solicitud.

La cola es FIFO por identificador de admisión entre los trabajos reclamables. El límite global de espera es configurable. `NEWGLASS_COMPARISON_MAX_ACTIVE` admite `0` (pausa) o `1` (valor inicial). El supervisor conserva la plaza compartida de TA-037 con FF y V1 y ejecuta un motor por vez. El alcance operativo es un proceso API en Linux. El reclamo registra el arranque de Linux y el espacio de nombres PID. Tras un cambio de cualquiera de los dos, la ausencia de un PID local no acredita su cierre. Una recuperación ambigua bloquea el despacho según TA-038.

Se aplican permisos efectivos de `rol_permisos`. El solicitante necesita `optimization.execute` para admitir o cancelar. El despachador revalida el permiso antes de reclamar. Si falta, cierra la solicitud sin lanzar procesos, deja el pedido disponible y registra `queue_permission_revoked=1` en los diagnósticos. Lectura, selección y confirmación conservan sus permisos y el aislamiento por propietario. Las operaciones de inventario conservan sus permisos independientes.

El `POST` V2 conserva `201`, `Location` y su cuerpo. Mientras el trabajo espera, la comparación conserva `EN_EJECUCION` y sus ejecuciones `QUEUED`. `GET /api/optimization/comparisons-v2/{id}/queue` expone `comparison_id`, `version` y `state` (`WAITING`, `RUNNING`, `TERMINAL` o `RECOVERY_BLOCKED`). El cupo por defecto es de ocho trabajos en espera, con valores entre 1 y 100. El cupo lleno devuelve `503 OPTIMIZER_BUSY` con `Retry-After`. La cancelación de un trabajo en espera resuelve el lote en una sola transacción sin señalizar procesos. La cancelación activa conserva la prueba de cierre de EN-UX-004.

Sprint 2 vincula este incremento con TA-037 (admisión y recursos), TA-038 (cierre y recuperación) y EN-UX-004 (cancelación). En 2B quedan pendientes la supervisión agregada de varias comparaciones, la exclusión distribuida también para FF/V1 y la retención visual entre procesos. Los resultados históricos siguen sujetos a sus reglas V3/V4.

## Fase 2B.1

FastAPI admite, reserva, consulta y confirma. Un servicio Linux externo reclama y ejecuta V2. La plaza se reserva en PostgreSQL junto con el propietario y la traza comercial de supervisor. La restricción `UNIQUE(slot)` permite muchos trabajos con plaza nula y una sola fila con plaza `1`. Cada escritura operativa del supervisor comprueba su token y vigencia dentro de la transacción.

El piloto es una elección de la base de datos. Tras la migración, el modo por defecto mantiene las corridas FF y V1. La activación optativa impide nuevos cálculos legacy en todos los procesos conectados a esa base. Requiere haber detenido los binarios 2A y verificar que ninguna ejecución anterior conserve procesos. La activación adopta únicamente trabajos 2A en espera sin propietario, preservando el JSON existente. Un registro anterior ambiguo aborta la activación completa. Los resultados terminados V3/V4 y sus recibos permanecen en las tablas comerciales.

El estado operativo es `WAITING`, `RUNNING`, `RECOVERY_BLOCKED` o `TERMINAL`. Perder el heartbeat durante 10 segundos bloquea el trabajo y retiene la plaza. El servicio puede reconciliar su propio cierre si los descendientes ya terminaron y su token sigue vigente. La fase no dispone de relevo automático. Una cancelación activa registra la intención en API y el worker la consulta en su heartbeat.

La API conserva los cuerpos e identidades de V2 y la consulta `/{id}/queue`. El historial y el resultado persistido se sirven desde cualquier instancia. Los eventos y visuales devuelven `410 VISUAL_DATA_UNAVAILABLE` en el piloto y preservan autorización y propiedad. Las vistas visuales compartidas y el aumento de capacidad pertenecen a las fases posteriores.

## Fase 2B.2

Cada reclamo reserva la única plaza y registra un intento con generación creciente, token opaco, identidad Linux y tiempo de PostgreSQL. Cambios del supervisor y resultados exigen token y generación vigentes bajo bloqueo. Vencer el heartbeat conserva la plaza y el pedido. Solo se recupera automáticamente un V2 con propietario anterior ausente en su mismo arranque y namespace y sin intención de spawn, o con evidencia durable de join de cada proceso registrado. Un intento sin despacho admite un solo reintento automático. Un intento con resultados acreditados conserva sus snapshots y termina las ejecuciones pendientes sin recalcular. La cancelación solicitada queda persistida también cuando el trabajo está bloqueado.

El incremento original de 2B.2 incorporó V1 y V2 al mismo índice de plaza de PostgreSQL. V1 conserva su API con clave de solicitud, los gestores de límites y el progreso local. FF mantuvo su modo legacy de una instancia hasta la extensión descrita a continuación.

## Fase 2B.2-FF

El piloto mixto incorpora FF individual por `POST /api/optimization/runs/durable`. La solicitud requiere `Idempotency-Key` por usuario, reserva los pedidos y ocupa un lugar en la misma cola FIFO de FF y V2. FF, V1 y V2 comparten la plaza activa. El worker separado calcula FF con entrada congelada y recupera un trabajo sin despacho una sola vez si acredita la salida de su propietario. Ante un despacho sin cierre durable, conserva `RECOVERY_BLOCKED`, el pedido y la plaza. La consulta de estado es privada por propietario y comprueba `optimization.view`. La confirmación conserva `optimization.confirm` y exige además evidencia de cierre para un FF durable.

El NDJSON de `/runs` sigue disponible en modo legacy. En el piloto mixto, FF se consulta por estado y por resultado persistido. La simulación mantiene el inventario y el resultado utiliza las mismas métricas corregidas. Se conserva la lectura de los resultados FF históricos y de los snapshots comparativos V3/V4.
