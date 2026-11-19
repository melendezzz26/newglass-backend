# ADR EN-CONC-001 / Fencing y recuperación 2B.2

## Decisión

La restricción única de la plaza sigue siendo global. PostgreSQL registra la generación y el token tanto en el trabajo como en su intento. Cada intento conserva la identidad del propietario, su alcance Linux y el cierre acreditado. Al migrar desde 2B.1, los trabajos con owner conservan su token como generación 1. El heartbeat histórico sirve como referencia del instante de importación del intento, dado que el esquema anterior no registraba el instante de reclamo. El proceso antiguo debe estar detenido antes de migrar el piloto.

Un supervisor obsoleto falla la comprobación de token, generación y vigencia antes de proceder con una escritura. La expiración conserva plaza y pedido en `RECOVERY_BLOCKED`. Cuando se demuestra que el supervisor salió en el mismo arranque y namespace y que lo registrado descarta un motor live, puede aplicarse una de dos recuperaciones. Si nunca se persistió intención de spawn, se permite un reintento conservando la misma comparación y clave. Si hay resultados con join acreditado, se preservan y se cierra el resto. Una intención de spawn sin identidad o join, un PID vivo, o una evidencia que no se pueda comparar conservan el bloqueo. El reintento no consume inventario.

V1 adquiere la misma plaza al admitir el agregado. Sigue ejecutándose en su instancia FastAPI de origen con los límites existentes. La reconexión por una API diferente recupera el resultado persistido y aplica el mismo dueño. Ante caída ambigua de V1, la plaza permanece reservada. La cabecera `X-Comparison-Recovery: BLOCKED` señala ese estado en su lectura. La retención visual conserva su alcance local. FF independiente requiere evolución del contrato de admisión y del ciclo de vida del stream para que pueda sumarse al piloto.

## Activación local del modo mixto

Usar exclusivamente el PostgreSQL desechable de loopback en esta fase. Detener primero binarios anteriores y verificar el cierre de cálculos activos. Reutilizar el entorno WSL2 descrito en el ADR de separación. Con las variables locales del piloto:

```bash
export PYTHON_DOTENV_DISABLED=1
python -m alembic upgrade head
python -m app.modules.optimization.worker_v2 --enable-mixed
```

El comando rechaza el cambio si encuentra una plaza activa. Ejecutar las API y los candidatos al worker como procesos independientes. El modo `--enable-pilot` conserva la admisión exclusivamente V2. Los dos perfiles conservan una única plaza.

## Acreditación operativa en Linux

1. Detener la admisión de nuevos trabajos en el entorno afectado y conservar comparación, generación e identidades de supervisor y motores. Revisar el `boot_id`, el inode del namespace PID y el tiempo de inicio Linux del proceso. El tiempo de inicio está en el campo 22 de `/proc/<pid>/stat`, después de interpretar los paréntesis del nombre. La ausencia de un PID en otro namespace o sin visibilidad suficiente no acredita cierre.
2. Si existe una unidad de servicio o un cgroup dedicado que contiene todos los descendientes desde su creación, detener esa unidad y verificar que su árbol quede con `populated 0` en `cgroup.events`. Correlacionar ese dato con la identidad y las trazas observadas. Un PID de supervisor ausente no sustituye la evidencia sobre los descendientes.
3. Registrar la evidencia, la comparación y la generación para revisión operativa. Si falta evidencia del join o del ámbito de procesos, dejar `RECOVERY_BLOCKED`. Esta fase no ofrece un comando que libere la plaza por declaración manual ni un mecanismo para sobrescribir snapshots. La recuperación que acepta el sistema usa exclusivamente la evidencia durable descrita arriba.
