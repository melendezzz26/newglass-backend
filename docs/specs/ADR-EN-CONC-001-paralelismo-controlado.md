# EN-CONC-001 / Piloto de dos plazas en Linux

PostgreSQL registra las plazas habilitadas. La migración `s2conc01b400` habilita únicamente la plaza 1 y conserva las asignaciones existentes. El registro de plaza 2 contiene la identidad y los límites del cgroup Linux acreditado. Una clave foránea valida que la plaza exista y `UNIQUE(slot)` conserva la exclusividad de cada identificador. Una exclusión GiST por rango impide que FF o V1 se solapen con cualquier otro cálculo, incluso cuando compiten por plazas distintas. V1 conserva su admisión inmediata, con rechazo por capacidad y sin cola. FF durable y V2 siguen usando la cola acotada. Si el primer trabajo es FF, el despachador espera a que ambas plazas queden libres.

Dos servicios Linux independientes pueden reclamar V2 cuando su identidad coincide con la acreditación almacenada. Cada worker conserva un único propietario local y los seis motores secuenciales. Una plaza con heartbeat vencido mantiene propiedad, generación y protección de stock. El otro worker puede reclamar únicamente una plaza libre. El cierre y la retirada de una plaza siguen requiriendo evidencia de procesos detenidos. Cambiar la capacidad exige cerrar todos los trabajos activos o bloqueados.

## Presupuesto de recursos

La capacidad PostgreSQL rige para toda la base. El presupuesto físico rige para el cgroup Linux del piloto. Los dos workers y sus descendientes deben pertenecer a la misma slice acreditada. La identidad incluye el arranque Linux, los espacios de nombres PID y de red, el UID y el inode del cgroup. El piloto defiere el cálculo que no puede acreditar esos datos. Ejecutar workers en hosts distintos exige otra política de recursos.

La slice exige RAM acotada entre 1.536 y 4.096 MiB, swap cero, cuota de dos a ocho CPU según afinidad efectiva y un máximo finito de tareas. Se comprueban los límites efectivos de los ancestros. El kernel limita RAM, CPU y tareas para el árbol completo. El supervisor mide la memoria del cgroup, RSS y procesos, CPU, presión y eventos OOM. La admisión se pausa al 80 % del límite de RAM. Al 90 %, o ante cambios de identidad, límites u OOM, cada supervisor señala a su propio trabajo para un cierre seguro. El fencing y los límites individuales de procesos, tiempo, IPC y cancelación siguen en vigor. Las dos identidades de servicio tienen exclusión local. Cada una abre como máximo dos conexiones PostgreSQL. El pool de cada API se dimensiona aparte.

## Reproducción sin cambiar servicios permanentes

En WSL2 con systemd de usuario, usar Python del proyecto y un PostgreSQL de loopback desechable. El ensayo crea y retira una slice efímera por caso. Se conservan las fases de migración y las pruebas de capacidad predeterminada en CI.

```bash
export PYTHON_DOTENV_DISABLED=1
export NEWGLASS_REQUIRE_POSTGRES=1
export NEWGLASS_TEST_PG_BIN="$(pg_config --bindir)"
export NEWGLASS_TEST_PARALLEL=1
python -m pytest tests/integration/optimization/test_parallel_lab.py -q -ra -s
```

Para un ensayo manual, montar una slice temporal `newglasslab-<identificador>.slice` con `MemoryMax=1700M`, `MemorySwapMax=0`, `CPUQuota=200%` y `TasksMax=64`. Con la base local migrada, activar primero `--enable-pilot` o `--enable-mixed`. Desde un servicio dentro de la slice, ejecutar `python -m app.modules.optimization.worker_v2 --lab-capacity 2 --lab-slice <slice>` con la base vacía de propietarios activos. Iniciar dos servicios dentro de la misma slice con `--lab-worker 1` y `--lab-worker 2`, ambos con `--lab-slice <slice>`. La capacidad inicial sigue siendo uno cuando se omite ese cambio.

Para devolver el laboratorio a una plaza, detener la admisión y obtener cierre verificable de ambos trabajos. Ejecutar `python -m app.modules.optimization.worker_v2 --lab-capacity 1` contra la misma base local. Un trabajo bloqueado conserva su plaza. Si hay un OOM, acreditar los cierres y reinscribir el entorno con los contadores actuales antes de volver a reclamar.
