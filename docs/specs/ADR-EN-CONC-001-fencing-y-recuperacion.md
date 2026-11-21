# ADR EN-CONC-001 / Fencing y recuperación 2B.2

## Decisión

La restricción única de la plaza sigue siendo global. PostgreSQL registra la generación y el token tanto en el trabajo como en su intento. Cada intento conserva la identidad del propietario, su alcance Linux y el cierre acreditado. Al migrar desde 2B.1, los trabajos con owner conservan su token como generación 1. El heartbeat histórico sirve como referencia del instante de importación del intento, dado que el esquema anterior no registraba el instante de reclamo. El proceso antiguo debe estar detenido antes de migrar el piloto.

Un supervisor obsoleto falla la comprobación de token, generación y vigencia antes de proceder con una escritura. La expiración conserva plaza y pedido en `RECOVERY_BLOCKED`. Cuando se demuestra que el supervisor salió en el mismo arranque y namespace y que lo registrado descarta un motor live, puede aplicarse una de dos recuperaciones. Si nunca se persistió intención de spawn, se permite un reintento conservando la misma comparación y clave. Si hay resultados con join acreditado, se preservan y se cierra el resto. Una intención de spawn sin identidad o join, un PID vivo, o una evidencia que no se pueda comparar conservan el bloqueo. El reintento no consume inventario.

V1 adquiere la misma plaza al admitir el agregado. Sigue ejecutándose en su instancia FastAPI de origen con los límites existentes. La reconexión por una API diferente recupera el resultado persistido y aplica el mismo dueño. Ante caída ambigua de V1, la plaza permanece reservada. La cabecera `X-Comparison-Recovery: BLOCKED` señala ese estado en su lectura. La retención visual conserva su alcance local.

## Extensión 2B.2-FF

El modo mixto admite FF individual mediante una ruta JSON separada. La transacción de admisión registra una clave única de FF por propietario, reserva los pedidos, congela la entrada y crea un trabajo `FF` en la cola compartida con V2. Los dos tipos consumen el mismo cupo de espera y se despachan por identificador de admisión. V1 conserva su admisión inmediata y recibe `503` si la plaza está ocupada. El índice `uq_optimization_worker_global_slot` protege los tres tipos incluso entre transacciones concurrentes. El [contrato HTTP de FF durable](../contracts/optimization-ff-durable.md) detalla la reconexión.

El servicio separado revalida `optimization.execute` antes de reclamar. El supervisor reutiliza el ejecutor secuencial con un único ámbito `FF` y mantiene los presupuestos base, incluido el máximo de cálculo de 120 segundos. El intento registra la intención con tiempo PostgreSQL antes de crear el hijo y registra PID y ticks de inicio antes de que pueda empezar el motor. Las escrituras requieren token, generación y heartbeat vigentes. Tras acreditar el join del árbol, la publicación del resultado, el cambio de estado del pedido, el cierre del intento y la liberación de la plaza ocurren en una sola transacción. Una caída después de esa transacción deja un resultado recuperable mediante la misma clave. Una caída posterior a la intención de despacho y anterior a la transacción de cierre conserva `RECOVERY_BLOCKED` y la reserva. La ausencia verificable del propietario en el mismo arranque y espacio de nombres, junto con la ausencia de intención de despacho, permite un reintento del mismo trabajo.

La migración `s2conc01b210` incorpora la clave FF y la evidencia de despacho. Deja intactos los snapshots anteriores. La reversión del esquema está disponible únicamente mientras no exista historial de FF durable. Actualiza API y worker juntos antes de admitir FF. Un worker anterior desconoce el tipo `FF` y puede impedir el despacho de la cola. Conserva un binario compatible para el rollback operativo si ya existen solicitudes durables.

## Progreso y stock en 2B.3

El journal tiene un contador por trabajo y filas pequeñas con referencias al motor. Las escrituras usan la transacción de admisión, despacho, resultado, cancelación o recuperación. Las consultas usan un cursor finito para que una conexión del navegador no retenga una sesión PostgreSQL mientras espera. Un snapshot comercial y la plaza operativa siguen siendo la fuente de estado aunque se hayan eliminado los eventos. La expiración de heartbeat conserva las protecciones de inventario con `RECOVERY_BLOCKED` hasta un cierre verificable. La migración reconstruye referencias de stock para los trabajos abiertos de FF, V1 y V2 que ocupan un registro operativo y conserva el historial publicado. [Reconexión y configuración](../contracts/optimization-distributed-progress.md)

## Activación local del modo mixto

Usar exclusivamente el PostgreSQL desechable de loopback en esta fase. Detener primero binarios anteriores y verificar el cierre de cálculos activos. Reutilizar el entorno WSL2 descrito en el ADR de separación. Con las variables locales del piloto:

```bash
export PYTHON_DOTENV_DISABLED=1
python -m alembic upgrade head
python -m app.modules.optimization.worker_v2 --enable-mixed
```

Al cambiar de perfil, el comando rechaza la operación si encuentra una plaza activa. Ejecutar las API y los candidatos al worker como procesos independientes. El modo `--enable-pilot` conserva la admisión exclusivamente V2. Los dos perfiles conservan una única plaza.

## Acreditación operativa en Linux

1. Detener la admisión de nuevos trabajos en el entorno afectado y conservar comparación, generación e identidades de supervisor y motores. Revisar el `boot_id`, el inode del namespace PID y el tiempo de inicio Linux del proceso. El tiempo de inicio está en el campo 22 de `/proc/<pid>/stat`, después de interpretar los paréntesis del nombre. La ausencia de un PID en otro namespace o sin visibilidad suficiente no acredita cierre.
2. Si existe una unidad de servicio o un cgroup dedicado que contiene todos los descendientes desde su creación, detener esa unidad y verificar que su árbol quede con `populated 0` en `cgroup.events`. Correlacionar ese dato con la identidad y las trazas observadas. Un PID de supervisor ausente no sustituye la evidencia sobre los descendientes.
3. Registrar la evidencia, la comparación y la generación para revisión operativa. Si falta evidencia del join o del ámbito de procesos, dejar `RECOVERY_BLOCKED`. Esta fase no ofrece un comando que libere la plaza por declaración manual ni un mecanismo para sobrescribir snapshots. La recuperación que acepta el sistema usa exclusivamente la evidencia durable descrita arriba.
