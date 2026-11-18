# ADR EN-CONC-001 / Separación API y worker

## Decisión

La fase usa PostgreSQL como cola y árbitro de capacidad. `optimization_worker_jobs` referencia una comparación por su clave primaria. El trabajo guarda estado, plaza, token y heartbeat. La traza de procesos, las confirmaciones, los pedidos y los resultados corresponden al agregado existente. El índice parcial ordena la espera por identificador y la restricción única de plaza respalda el reclamo transaccional.

FastAPI registra la intención de cancelación desde cualquier instancia. El servicio de cálculo usa el runner y reaper existentes y finaliza la plaza en la misma transacción que acredita el cierre de la comparación. Los dos procesos pueden desplegarse localmente de forma independiente. La primera fase reserva como máximo una plaza.

La política persistida en `optimization_worker_policy` es común a todas las APIs. Durante la activación, una transacción bloquea el cambio de modo, valida trabajos existentes y adopta pendientes sin supervisor. La admisión de FF/V1 mantiene su límite local en el modo legacy y toma un bloqueo compartido de la política al crear una corrida. El piloto la rechaza en la base de datos. Esta decisión deja la coordinación distribuida de FF/V1 para 2B.2.

Si quedan comparaciones V2 abiertas antes de activar el piloto, la admisión legacy también queda bloqueada. Las comparaciones 2A que nunca se reclamaron se pueden cancelar o adoptar al activar. Un propietario 2A no acreditado requiere intervención documentada.

El heartbeat usa el reloj de PostgreSQL. La expiración impide nuevas escrituras del supervisor y conserva la plaza en `RECOVERY_BLOCKED`. El siguiente worker no deduce cierre de la ausencia de un PID. La fase deja el relevo y la recuperación avanzada para 2B.2. Compartir eventos y visuales corresponde a 2B.3 y probar dos comparaciones simultáneas a 2B.4.

## Arranque del piloto en WSL2

Usar el Python WSL existente y un PostgreSQL local desechable. Configurar `DATABASE_URL` con la base de loopback y suministrar la clave de autenticación local por el entorno. `PYTHON_DOTENV_DISABLED=1` evita que el proceso tome una conexión de otro entorno. Antes del cambio, detener todos los procesos del backend anterior y acreditar el cierre de workers antiguos. El comando de activación exige un destino PostgreSQL de loopback.

```bash
export PYTHON_DOTENV_DISABLED=1
python -m alembic upgrade head
python -m app.modules.optimization.worker_v2 --enable-pilot
```

Ejecutar cada comando en una terminal separada, con el mismo entorno local:

```bash
# Terminal 1
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
# Terminal 2, segunda API opcional para la prueba
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --workers 1
# Terminal 3, servicio de cálculo
python -m app.modules.optimization.worker_v2
```

Un segundo candidato puede ejecutar el mismo comando del worker. PostgreSQL conserva una única plaza entre ambos. `NEWGLASS_COMPARISON_MAX_ACTIVE=0` pausa el reclamo de ese candidato. El límite de espera mantiene el valor de 2A. Cada candidato usa hasta dos conexiones con `max_overflow=0` y utiliza transacciones cortas. El log `worker_v2_closed` contiene CPU del proceso y sus hijos recolectados, tiempo de pared y RSS agregado observado por el runner.

Ante `RECOVERY_BLOCKED`, conservar el pedido y la identidad de procesos. Verificar el cierre con el procedimiento de recuperación antes de cualquier intervención administrativa. El comando de activación es idempotente. La migración impide retroceder si el piloto está activo o ya existe historia operativa. Para exposición futura, las tablas operativas tienen RLS y requieren un usuario de backend con acceso explícito o propietario del esquema.
