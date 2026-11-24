# Informe técnico: backend y seis motores de NewGlass

La rama depende temporalmente del PR #7. Este informe conserva la batería de
escala original y agrega observaciones de concurrencia con servidores reales.

## 1. Resumen ejecutivo

Los seis motores completaron y validaron 120 ejecuciones medidas en cuatro
cargas sintéticas. En este fixture homogéneo, FF tuvo la menor mediana de
tiempo para 100, 200 y 300 unidades; en 50 empató con WF a la resolución del
cronómetro. M6-BS fue más lento y tuvo mayor RSS del proceso en las medianas
observadas. Esto no establece un ganador para pedidos comerciales diversos.

Dos servidores HTTP independientes admitieron trabajos contra el mismo PostgreSQL
temporal. En un solo servidor, una segunda comparación fue rechazada por la
admisión. Se midieron los árboles de procesos locales; no se comprobó el
funcionamiento completo en dos dynos reales.

## 2. Objetivo y alcance

Se evaluaron FF, BF, WF, HF, M5 y M6-BS sin alterar sus heurísticas y sin usar
producción. Se reutilizaron el protocolo y oráculo del PR #7; no se repitieron
sus 270 mediciones. Esta rama sale de `test/S2-andro-calidad-algoritmos` y
depende del PR #7, abierto al comprobarlo el 24 de noviembre de 2026.

## 3. Arquitectura y contrato

Un trabajo es una comparación V2 aceptada que ejecuta seis motores secuencialmente.
`RunStatus` utiliza `EN_EJECUCION`, `COMPLETADA`, `SIN_SOLUCION` y `FALLIDA`;
V2 distingue `COMPLETE`, `PARTIAL` y `FAILED`. `COMPLETED`, `NO_FIT`, los
límites nativos y los errores de supervisor son causas, no estados equivalentes.
En tablas, `TIMEOUT` y `RESOURCE_LIMIT` son categorías de informe.

## 4. Los seis motores y casos discriminantes

| Motor | Caso discriminante | Decisión demostrada | Resultado |
|---|---|---|---|
| FF | 10×10 y 6×10; pieza 5×5 | Elige la primera fuente factible. | PASS |
| BF | Mismas fuentes y pieza | Elige 6×10, con menor capacidad libre posterior. | PASS |
| WF | Dos fuentes usadas y factibles | Escoge mayor capacidad libre; el caso integrado también difiere de FF. | PASS |
| HF | Bolsillo interior alcanzable | Coloca una pieza circular y conserva la variante frente a FF. | PASS |
| M5 | Dos fuentes usadas y factibles | Escoge mayor rectángulo recuperable y menor fragmentación del caso focal. | PASS |
| M6-BS | CP-01 | Explora alternativas y difiere de FF; repetición idéntica. | PASS |

Las pruebas de selector y de caso alcanzable ya existentes para WF, HF y M5 se
reutilizan. En HF se observaron 8 candidatos de bolsillo, 4243 anclas y una
aceptación; no hubo descarte por cobertura ni planchas. Se agotó el presupuesto
de búsqueda local y el fallback se activó en otras decisiones. No se afirma
optimalidad general. M6 reutiliza CP-01, ya suficientemente discriminante.

## 5. Validación geométrica

El oráculo verifica operaciones independientes sobre el raster canónico,
incluidos contención, solapamiento, margen, separación, giro, material, espesor,
áreas y residuales. Se añadieron corrupciones explícitas de solapamiento,
separación, margen, fuera de fuente, paso angular, orientación y área; el modelo
rechaza pieza duplicada, fuente ajena y material o espesor incompatible. Se
comprobó un caso válido exactamente en margen y separación.

| Validación | Positivo | Negativo | Resultado |
|---|---|---|---|
| Contención | disposición válida | fuera de fuente | PASS |
| Solapamiento y separación | separación exacta | solape y separación insuficiente | PASS |
| Margen | margen exacto | margen insuficiente | PASS |
| Rotación y paso | orientación rígida válida | geometría inconsistente y paso inválido | PASS |
| Vidrio y espesor | compatibles | incompatibles | PASS |
| Identidad y fuente | identidades de entrada | duplicada y fuente ajena | PASS |
| Conteos y área | conteos y áreas coherentes | total, colocadas y área alterados | PASS |

El contrato usa paso angular y geometría orientada, no una bandera inventada por
pieza. El oráculo comparte el rasterizador y no constituye una prueba continua
independiente.

## 6. Metodología

Los casos usan unidades cuadradas de 100×100 mm, planchas de 1000×1000 mm,
raster, margen y separación de 10 mm, paso angular de 5°, vidrio y espesor
iguales y un testigo completo validado. Son uno, dos, cuatro y cinco materiales
físicos respectivamente; todos están dentro del límite de 50 por tipo.
Misma entrada y política por motor, con hashes `input_sha256` y `problem_sha256`.
Un piloto y un calentamiento por combinación se excluyeron de las estadísticas;
se midieron cinco repeticiones por combinación. No se calcula p95.

La merma es `A − P − R` y el aprovechamiento `100 × P/A`. El tiempo de cálculo
proviene del producto. El tiempo de pared, CPU y RSS son del proceso completo,
incluidos inicio, preparación, testigo y validación. RSS no es memoria virtual;
el límite externo de 256 MiB es `RLIMIT_AS`. `avg_cpu_pct` usa 100 % por núcleo.
El detalle reproducible está en `experiments/sprint2_quality/scale_protocol.md`.

## 7. Capacidad observada y calidad

| Motor | 50 | 100 | 200 | 300 | Límite observado |
|---|---:|---:|---:|---:|---|
| FF | 5/5 | 5/5 | 5/5 | 5/5 | No alcanzado en este fixture. |
| BF | 5/5 | 5/5 | 5/5 | 5/5 | No alcanzado en este fixture. |
| WF | 5/5 | 5/5 | 5/5 | 5/5 | No alcanzado en este fixture. |
| HF | 5/5 | 5/5 | 5/5 | 5/5 | No alcanzado en este fixture. |
| M5 | 5/5 | 5/5 | 5/5 | 5/5 | No alcanzado en este fixture. |
| M6-BS | 5/5 | 5/5 | 5/5 | 5/5 | No alcanzado en este fixture. |

Todas las celdas significan `COMPLETE` y validación raster aprobada; hubo 120
mediciones, sin parciales ni errores observados. No establecen un límite práctico
de pedidos reales. La merma mediana de FF/BF/WF/HF/M5 fue respectivamente
186400, 338800, 578600 y 792000 mm² por escala. M6 registró 272000, 500200,
882800 y 1235600 mm². La calidad depende también de fuentes y recuperables.

## 8. Tiempo, CPU y RAM

| Motor | 50 ms | 100 ms | 200 ms | 300 ms | RSS 300 MiB | CPU 300 s |
|---|---:|---:|---:|---:|---:|---:|
| FF | 16 | 49 | 130 | 265 | 29.26 | 0.586 |
| BF | 17 | 65 | 216 | 409 | 29.45 | 0.717 |
| WF | 16 | 59 | 174 | 339 | 29.32 | 0.640 |
| HF | 20 | 54 | 138 | 273 | 29.32 | 0.570 |
| M5 | 17 | 56 | 171 | 336 | 29.31 | 0.644 |
| M6-BS | 548 | 1267 | 3406 | 6658 | 30.94 | 6.998 |

Valores medianos. Los tiempos son de cálculo; CPU y RSS incluyen otras fases del
proceso, por lo que pequeñas diferencias entre motores no prueban diferencias
de consumo del algoritmo. Mínimos y máximos están en `scale_summary.csv`.

## 9. Uno y dos trabajos

La prueba usa Uvicorn real en loopback, PostgreSQL 16 temporal, pedidos
sintéticos y una comparación V2 de seis motores por trabajo. Sin confirmaciones.

| Escenario | Resultado HTTP | Tiempo conjunto | CPU muestreada | RSS agregado pico |
|---|---|---:|---:|---:|
| R1: un servidor, un trabajo | 201; completo | 6087 ms | 3.62 s | 145.1 MiB |
| R2a: dos usuarios, un servidor | 201 y 503 `active_runs` | 5936 ms | 3.53 s | 146.4 MiB |
| R2b: mismo usuario, un servidor | 201 y 503 `active_runs_per_user` | 6392 ms | 3.88 s | 147.0 MiB |
| R3: dos servidores, dos trabajos | dos 201; ambos completos | 4901 ms | 7.21 s | 292.4 MiB |

En R3 las CPU usuario/sistema por árbol fueron 2.90/0.69 s y 2.95/0.67 s;
los RSS pico individuales fueron 147.4 y 145.1 MiB, máximos que pueden ocurrir
en instantes distintos. Los tiempos individuales observados por sondeo fueron
4899 y 4829 ms desde el comienzo conjunto. Las respuestas rechazadas incluyen
`request_accepted=false`. Los snapshots persistidos fueron consultables por el
otro servidor y no se consumió inventario.

El muestreo Linux de `/proc` ocurre cada 20 ms sobre servidor y descendientes,
incluido el worker; excluye PostgreSQL y el cliente de prueba. CPU de procesos
breves puede quedar subestimada; la suma RSS puede contar memoria compartida y
perder picos entre muestras. El tiempo incluye solicitudes, sondeo y cierre; los
tiempos individuales son instantes de observación, no timestamps exactos del
motor. Hay una sola observación por escenario: no son estimaciones robustas de
rendimiento ni justifican concluir que dos servidores son más rápidos.

En una prueba separada, la cancelación desde B de un trabajo propiedad de A
devolvió `503 RECOVERY_BLOCKED`; A terminó normalmente sin cancelación. Tras
detener controladamente A durante otro trabajo, B consultó una ejecución terminal
`FALLIDA`, con supervisor detenido, motivo `WORKER_STOPPED` y el mismo hash de
entrada. La recuperación y la cancelación descritas corresponden a estas únicas
pruebas, no a todos los posibles fallos distribuidos.

## 10. Heroku, Supabase y límites

El Procfile versionado inicia un worker Uvicorn por dyno. No se verificaron
número ni plan de dynos actuales. La admisión, locks y supervisor en memoria son
locales por proceso; PostgreSQL comparte snapshots, advisory locks y bloqueos
transaccionales de confirmación, pero no sustituye admisión distribuida. No se
demostró soporte funcional de dos dynos. El pool documentado es 5 conexiones
por proceso, sin overflow predeterminado: dos procesos podrían reservar hasta
10, sujeto a configuración efectiva. El máximo configurado es
`procesos × (DB_POOL_SIZE + DB_MAX_OVERFLOW)`, sin contar otros clientes. El
timeout documentado es 30 s. La shell de auditoría no tenía definidas esas tres
variables ni `DATABASE_URL`; esto no demuestra la configuración desplegada. No
se verificaron cuota ni pooler Supabase.
No se ejecutó Locust ni se dispuso de resultados de Fabricio para incorporarlos.

## 11. Defectos y limitaciones

No se corrigieron defectos de producción. La admisión local es una limitación
arquitectónica demostrada, no una prueba de corrupción. El generador anterior
de carga limita stock a 50 por material y no garantiza una plancha por unidad
en escalas grandes; el nuevo testigo elimina esa incertidumbre para este fixture.
La carga homogénea con raster de 10 mm, las cinco muestras y la contención del
entorno compartido limitan las conclusiones. El manifiesto registra el SHA de
los motores, correspondiente a la base de la rama; los archivos nuevos de
experimento estaban en el árbol de trabajo al medir.

## 12. Recomendaciones y siguientes pasos

FF es el punto de partida más rápido en estas cargas desde 100 piezas; a 50
empató con WF. No recomendar un motor universal a partir de cuadrados uniformes.
Añadir geometrías mixtas con raster comercial, repetir de forma controlada las
mediciones de concurrencia y coordinar con Fabricio resultados de carga HTTP.
Antes de dos dynos probar admisión global,
ownership, recuperación, cancelación, idempotencia y confirmación transaccional.

## 13. En palabras simples

Todos los motores acomodaron los cuadrados de esta prueba. El motor de búsqueda
M6 probó alternativas y tardó más. Dos procesos pueden empezar trabajos a la
vez porque cada uno lleva su propia cuenta; eso aún no demuestra que ejecutar
dos dynos sea seguro para todo el sistema.

## Anexos

Los CSV, JSON, manifiestos y gráficos de `results/` son evidencia reproducible.
La selección unitaria anterior contiene 531 pruebas aprobadas; la selección de
concurrencia contiene tres pruebas aprobadas y la prueba integrada de un trabajo
pasó por separado. La regresión de API/preflight/persistencia V2 tuvo 88 pruebas
aprobadas y una advertencia de deprecación de Starlette/TestClient. Los conjuntos
ejecutados anteriormente se solapan y no deben
sumarse como casos únicos. Las gráficas G1–G5 muestran tiempo, RSS, CPU, merma y
cobertura; G6 resume estados, G7 compara recursos de concurrencia y G8 la
completitud por escala. El registro complementario de esta continuación está en
`PRUEBAS.md`.
