# Sprint 2 - Andro Q.: calidad y algoritmos

Comparacion del catalogo productivo FF, BF, WF, HF/M4, M5/BRF y M6-BS/BS-2.
No modifica heuristicas ni utiliza copias historicas de los motores.

## Protocolo fijado antes de la bateria

- Linux/WSL2, Python 3.11; un proceso nuevo por ejecucion y sin concurrencia.
- EngineExecutionAdapter, sin callbacks; limites nativos productivos intactos.
- Limite externo identico: 120 s de calculo y 256 MiB de espacio virtual.
- Guardia del proceso: 300 s incluyendo importaciones y validacion posterior.
  Un fallo de validacion detiene la bateria y conserva el log; nunca se oculta.
- Un warm-up por caso y motor (repeticion 0, excluida de CSV y estadistica).
  Calienta archivos/caches del SO, no el cache interno del proceso siguiente.
- Cinco repeticiones medidas por caso/motor. Orden rotado entre repeticiones.
- Nueve casos iniciales: 270 mediciones y 54 warm-ups. CP-07 solamente si
  CP-01..06 no aportan diferencias utiles; cualquier ampliacion se documenta.
- Entrada inmutable reconstruida por el loader existente. input_sha256 incluye
  fingerprint del problema, minimos residuales y politica externa. Se conserva
  tambien problem_sha256 para separar identidad geometrica y protocolo.
- Nunca conectar a produccion ni confirmar cortes. La evidencia UI es una
  validacion separada, posterior a las mediciones; sus tiempos no se mezclan.

## Casos

| Caso | Proposito | Unidades | Stock |
|---|---|---:|---|
| EXP-SMALL-001 | Regresion congelada | Segun JSON original | Original |
| EXP-MEDIUM-001 | Regresion congelada | Segun JSON original | Original |
| EXP-COMPLEX-001 | Regresion congelada | Segun JSON original | Original |
| CP-01 | Comercial pequeno | 3 | Una plancha 1200 x 1800 mm |
| CP-02 | Comercial mediano | 11 | Dos planchas 1800 x 2400 mm |
| CP-03 | Comercial grande acotado | 24 | Tres planchas 1800 x 2400 mm |
| CP-04 | Rectangulos, circulos, poligonos convexos | 6 | Dos planchas 1200 x 1800 mm |
| CP-05 | Multi-plancha necesaria por area/geometria | 8 | Ocho planchas 1200 x 1800 mm |
| CP-06 | Prioridad de retazo y uso de plancha | 7 | Retazo 650 x 850 y dos planchas 1200 x 1800 mm |

Los casos comerciales son sinteticos con dimensiones realistas, no pedidos de
clientes. Configuracion comercial: margen 3 mm, separacion 3 mm, raster 1 mm,
paso 5 grados, vidrio 1, espesor 6 mm. Recuperacion minima 10 x 10 mm, igual a
la configuracion demo y benchmarks comerciales actuales. No es recomendacion
de tolerancias de fabricacion. Se aplica la misma politica a los seis motores.
Las tres regresiones conservan sus minimos historicos 1 x 1 mm y configuracion
original; se reportan por separado y no se agregan con comerciales.

## Reproduccion

Desde la raiz del backend, con requirements.txt instalado:

```bash
PYTHON_DOTENV_DISABLED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m experiments.sprint2_quality.run --output /tmp/newglass-quality-results
```

El directorio de salida debe estar vacio para una reproduccion independiente.
La reanudacion solo debe usar el mismo entorno, commit y protocolo. La evidencia
original conserva los archivos de cada ejecucion, incluidas las de calentamiento.

Para analizar (entorno separado, sin alterar dependencias de medicion):

```bash
python -m pip install -r experiments/sprint2_quality/requirements.txt
python experiments/sprint2_quality/report.py --evidence docs/evidence/sprint-02/andro-calidad-algoritmos
```

## Criterio de interpretacion

Primero validez, despues cobertura completa, calidad y coste temporal.
Mediana, minimo y maximo de tiempo; no p95 con cinco muestras. No hay ganador
absoluto predeterminado. Una igualdad es un resultado valido. La validacion
independiente reutiliza el oraculo de bits raster y enumeracion de rectangulos
de tests/optimization/final_audit_support.py, fuera del cronometro del adaptador.
Comparte rasterizador canonico: no constituye una prueba geometrica continua.

### Coste del verificador independiente

Durante el calentamiento de CP-05, FF tardo 1723 ms de calculo, mientras el
oraculo original consumio mucho mas tiempo recorriendo columnas vacias.
Se acelero exclusivamente la enumeracion exhaustiva de intervalos de filas
del oraculo, saltando rachas de bits. Conserva area y todos los desempates y
es distinto del algoritmo productivo de histogramas. La prueba de equivalencia
contrasta 200 mascaras pseudoaleatorias reproducibles y bordes con el original.
Tambien compara ambos verificadores completos sobre rectangulos, triangulos
asimetricos y formas mixtas pequenas, varias fuentes, margen y separacion;
ambos rechazan una colocacion fuera del material. Esta es una optimizacion
metodologica del oraculo de validacion, no del rendimiento de los motores.
Las primeras corridas usaron el original y las siguientes su equivalente;
ambos se ejecutan despues de cerrar computation_time_ms. No se cambiaron
entradas, motores, configuracion ni numero de repeticiones por este motivo.

El README general describe el comparador V1 concurrente; V2 es secuencial.
Algunos comentarios historicos mencionan 180 s globales, pero el presupuesto
actual por defecto es 900 s. Este experimento aisla cada motor con 120 s;
no modifica documentacion general ni mide escalamiento, seguridad o HTTP.
