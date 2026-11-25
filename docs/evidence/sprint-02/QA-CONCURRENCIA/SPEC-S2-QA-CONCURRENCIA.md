# SPEC-S2-QA-CONCURRENCIA — Evaluación cuantitativa de la concurrencia con Locust

## Información general

| Campo | Valor |
|---|---|
| ID | S2-QA-CONCURRENCIA |
| Tareas | T01 SPEC · T02 arnés Locust (`load/`) · T03 ejecución E1–E3 · T04 gráficos · T05 evidencia |
| Sprint | Sprint 2 |
| Estado | **Implemented** (arnés, corridas y evidencia en la rama). Pasa a *Verified* después de la revisión y el merge. |
| PBI relacionado | Modelo de calidad ISO/IEC 25010 (EN-QA-001): Eficiencia de desempeño (TA-QA-002) y Fiabilidad (TA-QA-004) |
| Responsable | Fabricio Danniel Aguilar Chavez (`fabrilar1`) |
| Reviewer | Andro Joseph Quispe Cesias (distinto del autor) |
| Ubicación | `newglass-backend`, `docs/evidence/sprint-02/QA-CONCURRENCIA/` en la rama `test/S2-QA-concurrencia-locust` (sin PR por ahora). No se modifica ningún otro archivo de la repo. |
| Base medida | `newglass-backend` `main` en `aa6883f` (merge del PR #22), copiado con `git archive` a WSL2 |
| Fecha | 25/11/2026 |
| Evidencia | [README.md](README.md) con `resultados/` y `graficos/` |
| Referencias | [SPEC-EN-CONC-001](../../../specs/SPEC-EN-CONC-001.md) · [ADR worker separado](../../../specs/ADR-EN-CONC-001-worker-separado.md) · [ADR fencing](../../../specs/ADR-EN-CONC-001-fencing-y-recuperacion.md) · [ADR paralelismo](../../../specs/ADR-EN-CONC-001-paralelismo-controlado.md) · [SPEC-EN-SEC-001](../../../specs/SPEC-EN-SEC-001-limite-intentos-login.md) · [Contrato de progreso](../../../contracts/optimization-distributed-progress.md) · [Informe de Andro](../backend-six-engines/Informe_Backend_Seis_Motores_Escalabilidad_NewGlass.md) · Modelo ISO 25010 v1.3 y matriz TA-QA-001 (monorepo `optimizador-vidrio`, métricas MP-01 a MP-06) |

## 1. Objetivo

Medir con carga HTTP real y repetida cómo se comporta NewGlass cuando **varios usuarios lo usan
al mismo tiempo**: cuánto tarda en responder, cuántas peticiones por segundo atiende, cuántos
usuarios soporta dentro del objetivo, cuánta CPU y memoria consumen la API y el worker, cómo se
comporta la cola durable de comparaciones V2 y si dos confirmaciones simultáneas descuentan el
stock una sola vez. Las métricas siguen la nomenclatura de ISO/IEC 25023 y se ubican en las
características de ISO/IEC 25010:2023.

### Por qué la concurrencia se evalúa en Eficiencia de desempeño, con apoyo de Fiabilidad

ISO/IEC 25010:2023 no tiene una característica llamada "concurrencia". La concurrencia es una
**condición de carga**: lo que cambia con ella es el tiempo de respuesta (*comportamiento
temporal*), el consumo de CPU y memoria (*utilización de recursos*) y el número de usuarios o
peticiones que el sistema atiende dentro de un objetivo (*capacidad*). Esas tres son las
subcaracterísticas de **Eficiencia de desempeño**, y ISO/IEC 25023 define para ellas las medidas
PTb (*time behaviour*), PRu (*resource utilization*) y PCa (*capacity*). Por eso la medición
principal va ahí.

La concurrencia también puede romper el sistema: errores 5xx bajo carga, trabajos que se quedan
sin terminar o un descuento doble de stock. Eso es **Fiabilidad** (*ausencia de fallos* y
*tolerancia a fallos*): la tasa de fallos, la proporción de comparaciones completas (MP-05) y la
integridad de la confirmación concurrente apoyan la evaluación sin ser su eje.

## 2. Alcance

### Incluye

- Arnés reproducible en `load/` (junto a esta SPEC): `locustfile.py`, orquestador, datos sintéticos,
  muestreo de recursos, análisis y gráficos, con su propio `requirements-locust.txt`. Se ejecuta
  sobre una copia del backend en WSL2, sin modificar ningún archivo de las repos.
- Tres escenarios (E1 lecturas, E2 comparaciones V2 en cola, E3 confirmación concurrente) con
  3 repeticiones por nivel y CSV + HTML de Locust por corrida.
- Muestreo de CPU y RSS de la API y del worker (con sus descendientes) cada 200 ms.
- Gráficos PNG, tabla de métricas ISO 25023 con veredicto y comparación con la evidencia de Andro.

### Fuera de alcance

- Heroku, GitHub Pages y la Supabase de Staging: **ninguna carga sale de loopback**.
- Cambiar código de producción, límites, cola, pool o migraciones.
- Agregar Locust a `requirements.txt` de producción.
- Dos plazas activas, varios workers o varios dynos (piloto 2B.4).
- Estrés hasta la caída, pruebas de larga duración (*soak*) y recuperación ante fallos
  (cubiertas por EN-CONC-001 y TA-QA-004).

## 3. Actor y precondiciones

**Actor:** QA del equipo (ejecuta el arnés en WSL2).

**Precondiciones:**

- WSL2 con binarios de PostgreSQL 16 accesibles por `NEWGLASS_TEST_PG_BIN`.
- Entorno virtual de pruebas con `requirements.txt` más `load/requirements-locust.txt`.
- Ninguna variable apunta a una base remota: el orquestador crea su propio clúster PostgreSQL en
  `127.0.0.1` y rechaza cualquier destino que no sea loopback.

## 4. Entorno y datos

| Elemento | Valor |
|---|---|
| Sistema | WSL2, Ubuntu 24.04, kernel 6.18 (Windows 11 Pro como anfitrión) |
| Hardware | Intel Core i9-14900HX, 32 CPU lógicas visibles en WSL2, 7,8 GB de RAM asignados a WSL2 |
| Python | 3.12.3 del entorno de pruebas (producción declara 3.11.9; ver D-02) |
| PostgreSQL | 16.15, clúster temporal propio, solo `127.0.0.1`, autenticación `trust` local |
| API | `uvicorn main:app --workers 1` en `127.0.0.1` (igual que el `Procfile`), pool por defecto (5, sin overflow) |
| Worker | `python -m app.modules.optimization.worker_v2`, piloto durable activado, una plaza activa |
| Cola | Valores por defecto: `NEWGLASS_COMPARISON_QUEUE_CAPACITY=8`, `NEWGLASS_COMPARISON_MAX_ACTIVE=1` |
| Progreso | Valores por defecto: 4 lectores simultáneos y 24 lecturas/s por API, `poll_after_ms=1000` |
| Login | Valores por defecto de EN-SEC-001 (5 fallos / 15 min). Cada usuario Locust inicia sesión **una sola vez** en `on_start` con credenciales válidas |
| Secretos | `SECRET_KEY` y la contraseña sintética se generan al vuelo en memoria y no se escriben en disco ni en la evidencia |

| Dato sintético | Contenido |
|---|---|
| Cuentas | 80 por rol para E1 (Administrador, Almacenero, Operario), 10 Operarios para E2 y 1 Operario dueño para E3. Usuarios `A`, `L`, `O`, `C`, `K` + 8 dígitos |
| Lecturas (E1) | 300 pedidos de 2 piezas, 40 clientes, 60 planchas y 120 retazos en un material propio |
| Comparación (E2) | Un pedido por cuenta, 50 piezas de 100 × 100 mm, una plancha de 1000 × 1000 mm con cantidad 2, raster, margen y separación de 10 mm, paso de 5°: **el mismo fixture de la observación de Andro** |
| Confirmación (E3) | 20 pedidos de 10 piezas de 100 × 100 mm, una plancha de 1000 × 1000 mm con cantidad 40 en un material propio |

## 5. Escenarios

| ID | Qué se mide | Niveles | Repeticiones | Duración y ramp-up |
|---|---|---|---|---|
| E1 | Lecturas GET con mezcla de roles: pedidos (lista y detalle), planchas, retazos, tipos de vidrio, clientes, usuarios, roles, historial de comparaciones y `/api/auth/me`, según los permisos de cada rol | 1, 5, 10, 25 y 50 usuarios | 3 | Ramp-up a `max(N/10, 1)` usuarios/s (≤ 10 s), luego **ventana estable ≥ 60 s** (`--run-time 75s`). Tiempo de reflexión 0,2–1,0 s. Pesos de rol 3:3:4 |
| E2 | Comparación V2 de seis motores con el worker separado: admisión, espera en `WAITING`, tiempo hasta `TERMINAL`, FIFO, avance automático y códigos 201/429/503 | 1, 2, 4 y 8 usuarios simultáneos, más 10 para observar la cola llena | 3 | Todos los usuarios inician sesión, esperan una barrera y envían el `POST` a la vez (`-r N`). Sondeo de `/progress` respetando `poll_after_ms` y `Retry-After`. Al terminar se **libera** cada comparación (`/release`); no se confirma |
| E3 | Dos sesiones confirman al mismo tiempo la misma comparación terminada | 2 sesiones | 3 corridas × 20 = 60 | Por repetición: admitir, esperar `TERMINAL`, seleccionar, barrera y dos `POST /confirm` simultáneos con claves distintas. Luego se verifica PostgreSQL |

| E1-X *(exploratorio, descartado)* | Igual que E1, para buscar el punto de degradación (PCa-2) | 100, 200 y 400 usuarios | 3 | Igual que E1, una sola API para todas las corridas: desde la segunda arrastra el bloqueo anterior. Se conserva como evidencia |
| E1-R *(exploratorio)* | Igual que E1, con API, worker y base **nuevos en cada repetición** y sonda de salud a +5, +35 y +65 s | 75 y 100 usuarios | 3 | Igual que E1 |
| E1-S *(exploratorio)* | 100 usuarios con ramp-up lento, para separar el efecto de la ráfaga de logins | 100 usuarios | 3 | 2 usuarios/s (50 s), ventana estable ≥ 60 s (`--run-time 115s`), pila nueva por repetición |
| E2b *(exploratorio)* | Igual que E2, con envíos escalonados cada 400 ms para que no choquen en la admisión y se observe la cola llena | 10 usuarios | 3 | Usuario *i* envía a los *i* × 400 ms de la barrera |

Durante cada corrida se muestrean CPU y RSS de la API y del worker cada 200 ms, y la diferencia
acumulada entre el reloj de pared y el monótono (saltos de hora de WSL2).

## 6. Reglas (RN)

- **RN-01:** Toda carga va a `127.0.0.1`. El `locustfile` y el orquestador abortan con otro host
  o con un `DATABASE_URL` que no sea loopback.
- **RN-02:** Un usuario Locust inicia sesión una sola vez, en `on_start`, con credenciales
  válidas. No hay intentos fallidos de login, por lo que el límite de EN-SEC-001 no se activa.
- **RN-03:** Las métricas de E1 se calculan solo sobre la ventana estable y excluyen el login.
- **RN-04:** Un 503 `OPTIMIZER_BUSY` (cola llena) o un 429 `PROGRESS_BUSY` (presupuesto de
  progreso) con `Retry-After` es un **rechazo controlado**: el sistema protege su capacidad. Todo
  otro 5xx es un **fallo**.
- **RN-05:** E2 respeta el contrato de progreso: no vuelve a consultar antes de `poll_after_ms`
  (1000 ms) cuando está al día y espera `Retry-After` ante un 429.
- **RN-06:** E2 no confirma ni consume inventario; E3 es el único escenario que confirma.
- **RN-07:** Un percentil 95 con menos de 100 observaciones se informa con la marca "muestra
  insuficiente", como pide MP-03 de la matriz TA-QA-001.

## 7. Métricas ISO/IEC 25023, fórmulas y umbrales

| ID | Característica / subcaracterística | Medida | Fórmula | Unidad | Umbral (Cumple si…) | Escenario |
|---|---|---|---|---|---|---|
| PTb-1 | Eficiencia · Comportamiento temporal | Tiempo medio de respuesta (equivale a MP-01/MP-02/MP-03) | Media = Σ tᵢ / n; p50, p95, p99 = valor en la posición ⌈p·n⌉ de los tiempos ordenados | ms | Se informa; el veredicto lo da PTb-2 | E1, E2 |
| PTb-2 | Eficiencia · Comportamiento temporal | Adecuación del tiempo de respuesta | X = p95 medido / objetivo. Objetivo: lecturas 2000 ms; admisión de comparación 2000 ms | razón | X ≤ 1 | E1, E2 |
| PTb-5 | Eficiencia · Comportamiento temporal | Rendimiento (*throughput*) | X = peticiones completadas en la ventana / duración de la ventana. Eficiencia = X / (N / (reflexión media + tiempo medio con 1 usuario)) | req/s | Eficiencia ≥ 0,90 (el sistema atiende la demanda ofrecida) | E1 |
| PCa-1 | Eficiencia · Capacidad | Capacidad de procesamiento | Máximo de PTb-5 entre los niveles que cumplen PTb-2, 0 % de 5xx y eficiencia ≥ 0,90 | req/s | Se alcanza en el nivel más alto ensayado (50 usuarios); si no, se informa el nivel donde degrada | E1 |
| PCa-2 | Eficiencia · Capacidad | Capacidad de acceso de usuarios | Máximo N tal que las 3 repeticiones tienen p95 ≤ objetivo y 0 % de 5xx | usuarios | N ≥ 50 en lecturas | E1 |
| PCa-cola | Eficiencia · Capacidad | Capacidad de la cola de comparaciones | Solicitudes simultáneas admitidas (201) y rechazadas (503) por nivel | solicitudes | Hasta 8 simultáneas: 100 % admitidas. Con 10: el exceso recibe 503 `OPTIMIZER_BUSY` con `Retry-After` | E2 |
| PRu-1 | Eficiencia · Utilización de recursos | CPU media y pico | CPU % = Δ(tiempo de CPU usuario + sistema del árbol) / Δt × 100 (100 % = un núcleo) | % de un núcleo | API: media ≤ 80 % en todos los niveles que cumplen PCa-2. Worker: se informa | Todos |
| PRu-2 | Eficiencia · Utilización de recursos | RSS medio y pico (equivale a MP-04) | Suma sincronizada de RSS del proceso y sus descendientes | MiB | API: pico ≤ 512 MiB (dyno Basic). Worker y motores: pico ≤ 448 MiB (`NEWGLASS_COMPARISON_AGGREGATE_MIB`) | Todos |
| RFa | Fiabilidad · Ausencia de fallos | Tasa de fallos | 5xx no controlados / total de peticiones × 100 | % | 0 % | Todos |
| RRc | Eficiencia · Capacidad (control) | Tasa de rechazos controlados | (503 `OPTIMIZER_BUSY` + 429 `PROGRESS_BUSY`) / total × 100 | % | E1: 0 %. E2: solo con 10 usuarios (503) y en `/progress` (429), siempre con `Retry-After` | E1, E2 |
| TQ-1 | Eficiencia · Comportamiento temporal | Tiempo de espera en cola | t(RUNNING) − t(WAITING), con instantes de PostgreSQL del journal de progreso | s | Se informa por posición en la cola | E2 |
| TQ-2 | Eficiencia · Comportamiento temporal | Tiempo total por comparación | t(TERMINAL) − t(WAITING) (servidor) y t(TERMINAL observado) − t(envío) (cliente) | s | Se informa por posición en la cola | E2 |
| RFt-1 | Fiabilidad · Tolerancia a fallos | Orden FIFO y avance automático | Inversiones de orden entre identificador de admisión e instante de `RUNNING`; trabajos que avanzan sin reenviar | conteo / % | 0 inversiones y 100 % de avance automático | E2 |
| MP-05 | Fiabilidad · Ausencia de fallos | Comparaciones completas | Comparaciones `COMPLETADA` con seis motores completos / admitidas × 100 | % | 100 % | E2, E3 |
| RIn-1 | Fiabilidad · Ausencia de fallos | Integridad concurrente | Repeticiones cuyo efecto en stock, movimientos y retazos difiere del recibo persistido de **una** confirmación | conteo | 0 de 60 | E3 |
| RFa-conf | Fiabilidad · Ausencia de fallos | Respuesta correcta a la confirmación concurrente | Repeticiones con exactamente una 200 y una 409 sin efecto / total | % | 100 % | E3 |

## 8. Decisiones (D) y preguntas abiertas (P)

| ID | Decisión | Motivo |
|---|---|---|
| D-01 | En E3 las "dos personas" son **dos sesiones independientes del mismo Operario dueño** (dos logins, dos claves de idempotencia). | Las comparaciones son privadas por propietario: otra cuenta recibe 404 (SPEC-EN-CONC-001, `get_owned`). El riesgo real es el mismo usuario en dos pestañas o dispositivos. |
| D-02 | Se usa el Python 3.12.3 del entorno WSL2 existente, no 3.11.9. | Instalar otro intérprete estaba fuera de lo autorizado. Los pines de `requirements.txt` se instalaron sin cambios; se declara como limitación. |
| D-03 | Los percentiles de E1 se calculan con un registro propio por petición (no con el agregado de Locust), sobre la ventana estable y sin login. | Locust mezcla el ramp-up y el login en su fila *Aggregated*. Los CSV y HTML de Locust se conservan como evidencia cruda. |
| D-04 | E2 usa el fixture de 50 piezas de la observación de Andro. | Permite comparar directamente el caso de 2 usuarios. |
| D-05 | Los objetivos de 2000 ms para lecturas y admisión vienen del plan de la tarea; los umbrales de memoria vienen de `.env.example` (512 MiB dyno Basic y 448 MiB agregados del comparador). | No hay otro objetivo aprobado en el modelo de calidad. |
| D-06 | Un único proceso API, como el `Procfile`. | Medir la configuración que hoy se despliega. |
| D-07 | La API, el worker y PostgreSQL se reutilizan entre las repeticiones de un escenario; entre escenarios se reinicia todo. | Evita que los tiempos incluyan arranques; los datos de E1 no cambian y E2 deja los pedidos `PENDIENTE` al liberar. |
| D-09 | E3 pasa de 20 a 3 corridas × 20 repeticiones (60 pedidos). | La primera corrida de 20 mostró una respuesta 500 con la confirmación aplicada; se necesitaba más muestra y diagnóstico (instantes de la traza y lectura posterior). La corrida inicial se conserva como evidencia. |
| D-10 | Se agregan E1-X, E1-R, E1-S y E2b como complementos **exploratorios**, fuera de los criterios de aceptación. E1-X se descarta como medición porque reutilizó la API entre corridas; E1-R la reemplaza con pila nueva por repetición. | E1 cumplió con holgura hasta 50 y no permitía hallar la capacidad máxima; en E2 los envíos simultáneos chocan en la admisión (409) y la cola nunca se llena, por lo que el 503 no podía observarse. |
| D-11 | La integridad de E3 se juzga por el **efecto** en PostgreSQL frente al recibo persistido, y la respuesta HTTP se mide aparte (RFa-conf). | Una respuesta 500 con la confirmación aplicada no es un descuento doble, pero sí un fallo de fiabilidad: deben contarse por separado. |
| D-08 | La SPEC, el arnés y la evidencia van juntos en `docs/evidence/sprint-02/QA-CONCURRENCIA/` (no en `docs/specs/` ni en `experiments/`) y no se toca ningún otro archivo de la repo. | Indicación del responsable: un solo commit de evidencia, revisable sin riesgo para el código. Moverlos a su ubicación habitual es P-03. |

| ID | Pregunta abierta | Estado |
|---|---|---|
| P-01 | ¿El equipo acepta 2000 ms como objetivo de p95 para lecturas y admisión en el modelo de calidad? | Abierta; se propone en el PR. |
| P-02 | Repetir E1 y E2 en Staging/Heroku con autorización del equipo y sin tocar Supabase de producción. | Abierta; fuera del alcance de esta tarea. |
| P-03 | ¿Se mueve la SPEC a `docs/specs/` y el arnés a `experiments/load/` al integrar? | Abierta; la decide el equipo en la revisión. |

## 9. Criterios de aceptación

| ID | Criterio | Tarea |
|---|---|---|
| CA-01 | La SPEC define escenarios, métricas con fórmula, umbrales y la justificación ISO. | T01 |
| CA-02 | `load/run_all.sh` reproduce todo con un comando y no agrega dependencias a producción. | T02 |
| CA-03 | E1 tiene 5 niveles × 3 repeticiones, con ventana estable ≥ 60 s y CSV + HTML de Locust por corrida. | T03 |
| CA-04 | E2 tiene 5 niveles × 3 repeticiones y registra admisión, espera, total, FIFO, avance y códigos. | T03 |
| CA-05 | E3 tiene al menos 20 repeticiones (se ejecutaron 60) verificadas contra PostgreSQL. | T03 |
| CA-06 | Hay CPU y RSS de API y worker cada 200 ms en todas las corridas. | T03 |
| CA-07 | Hay gráficos PNG y una tabla de métricas con valor, umbral y veredicto. | T04 |
| CA-08 | La evidencia documenta entorno, resultados, comparación con Andro, limitaciones y recomendaciones, sin secretos. | T05 |

## 10. Pruebas previstas

- Corridas E1, E2 y E3 descritas en §5 (no son pruebas de pytest: no cambian el CI).
- Verificación posterior de cada corrida E2: pedidos de nuevo `PENDIENTE`, plaza libre y cola vacía.
- Verificación de E3 en PostgreSQL: stock, movimientos `CONSUMO_OPTIMIZACION` y retazos por
  comparación frente al recibo de la única confirmación aceptada.
- No se modifica código del backend: no corresponde regresión de pytest por esta tarea.

## 11. Evidencias requeridas

- `README.md` (esta carpeta) con entorno, tabla de métricas, gráficos,
  comparación con Andro, interpretación, limitaciones y recomendaciones.
- `resultados/`: CSV y HTML de Locust por corrida, registro por petición, recursos y resúmenes.
- `graficos/`: PNG generados con matplotlib.

## Historial de estado

| Fecha | Versión | Estado | Cambio |
|---|---|---|---|
| 25/11/2026 | 1.0 | Draft | Objetivo, escenarios, métricas, umbrales y decisiones. |
| 25/11/2026 | 1.1 | Implemented | Arnés, corridas E1–E3 y evidencia locales. |
| 25/11/2026 | 1.2 | Implemented | E3 a 3 × 20, E1-X y E2b exploratorios, RFa-conf (D-09 a D-11). |
| 25/11/2026 | 1.3 | Implemented | E1-R y E1-S exploratorios; evidencia integrada en `docs/evidence/sprint-02/QA-CONCURRENCIA/` (D-08). |
