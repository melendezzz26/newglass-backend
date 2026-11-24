# Informe de avance: backend y seis motores de NewGlass

Estado: **PARCIAL**. La rama depende temporalmente del PR #7. Este informe
conserva los resultados obtenidos y señala las mediciones aún pendientes.

## 1. Resumen ejecutivo

Los seis motores completaron y validaron 120 ejecuciones medidas en cuatro
cargas sintéticas. En este fixture homogéneo, FF tuvo la menor mediana de
tiempo para 100, 200 y 300 unidades; en 50 empató con WF a la resolución del
cronómetro. M6-BS fue más lento y tuvo mayor RSS del proceso en las medianas
observadas. Esto no establece un ganador para pedidos comerciales diversos.

Dos coordinadores en procesos diferentes admitieron simultáneamente trabajos
contra el mismo PostgreSQL temporal. En un solo proceso, una segunda comparación
fue rechazada por la admisión. No se midieron CPU y RAM de esos escenarios de
concurrencia ni se comprobó el funcionamiento completo en dos dynos.

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

| Motor | Decisión observada en pruebas |
|---|---|
| FF | En fuentes 10×10 y 6×10 con pieza 5×5 elige la primera. |
| BF | En el mismo caso elige la fuente 6×10 con menor capacidad libre posterior. |
| WF | En el caso existente con dos fuentes 4×4 ya usadas, elige la fuente que deja más capacidad libre; FF elige la primera. |
| HF | El caso integrado existente coloca una pieza circular en un bolsillo interior y conserva la variante frente a FF; ambas soluciones completan. |
| M5 | En las dos fuentes usadas del caso integrado elige el residual rectangular de 4 celdas frente al de 3; FF toma la primera. |
| M6-BS | En el CP-01 existente explora más alternativas que piezas y obtiene una disposición distinta de FF; ambos completan. |

Las pruebas de selector y de caso alcanzable ya existentes para WF, HF y M5 se
reutilizan. No se afirma optimalidad general. El caso M6 reutiliza un caso
previo: no se hizo la búsqueda sistemática solicitada del primer contraejemplo.

## 5. Validación geométrica

El oráculo verifica operaciones independientes sobre el raster canónico,
incluidos contención, solapamiento, margen, separación, giro, material, espesor,
áreas y residuales. Se añadieron corrupciones explícitas de solapamiento,
separación, margen, fuera de fuente, paso angular, orientación y área; el modelo
rechaza pieza duplicada, fuente ajena y material o espesor incompatible. Se
comprobó un caso válido exactamente en margen y separación.

Quedan pendientes un caso independiente de conteo inconsistente y la revisión
específica de giro prohibido según el contrato de entrada. El oráculo comparte
el rasterizador y no constituye una prueba continua independiente.

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

La prueba integrada de un trabajo comprobó seis ejecuciones, snapshot común,
persistencia y ausencia de consumo de inventario. En un mismo coordinador, una
segunda solicitud fue rechazada con `OPTIMIZER_BUSY`: `active_runs` para otro
usuario y `active_runs_per_user` para el mismo. Con dos procesos y PostgreSQL
temporal compartido, dos coordinadores admitieron trabajos con pedidos distintos,
IDs distintos y seis ejecuciones persistidas cada uno; no hubo movimientos ni
consumo de planchas. Los coordinadores son reales, pero la prueba no levanta dos
servidores HTTP. Falta medir tiempo, CPU y RSS de C1/C2/C3 y verificar recuperación
y cancelación entre procesos. Por ello no se presenta un gráfico G7 cuantitativo.

## 10. Heroku, Supabase y límites

El Procfile versionado inicia un worker Uvicorn por dyno. No se verificaron
número ni plan de dynos actuales. La admisión, locks y supervisor en memoria son
locales por proceso; PostgreSQL comparte snapshots, advisory locks y bloqueos
transaccionales de confirmación, pero no sustituye admisión distribuida. No se
demostró soporte funcional de dos dynos. El pool documentado es 5 conexiones
por proceso, sin overflow predeterminado: dos procesos podrían reservar hasta
10, sujeto a configuración efectiva. No se verificaron cuota ni pooler Supabase.
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
Medir procesos y árbol de trabajadores para C1/C2/C3, añadir geometrías mixtas
con raster comercial, completar los casos negativos pendientes y coordinar con
Fabricio resultados de carga HTTP. Antes de dos dynos probar admisión global,
ownership, recuperación, cancelación, idempotencia y confirmación transaccional.

## 13. En palabras simples

Todos los motores acomodaron los cuadrados de esta prueba. El motor de búsqueda
M6 probó alternativas y tardó más. Dos procesos pueden empezar trabajos a la
vez porque cada uno lleva su propia cuenta; eso aún no demuestra que ejecutar
dos dynos sea seguro para todo el sistema.

## Anexos

Los CSV, JSON, manifiestos y gráficos de `results/` son evidencia reproducible.
La selección unitaria ejecutada contiene 531 pruebas aprobadas; la selección de
concurrencia contiene tres pruebas aprobadas y la prueba integrada de un trabajo
pasó por separado. La regresión de API/preflight/persistencia V2 tuvo 88 pruebas
aprobadas y una advertencia de deprecación de Starlette/TestClient. Los conjuntos
ejecutados anteriormente se solapan y no deben
sumarse como casos únicos. Las gráficas G1–G5 muestran tiempo, RSS, CPU, merma y
cobertura; G6 resume estados y G8 la cobertura de completitud por escala. La
ausencia de G7 corresponde a la medición de concurrencia pendiente.
