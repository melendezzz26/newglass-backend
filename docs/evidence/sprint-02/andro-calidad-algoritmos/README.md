# Sprint 2 - Andro Quispe: calidad y algoritmos

Este paquete mide los seis motores actuales, sin cambiar algoritmos ni frontend.
No mezcla resultados de los spikes historicos con ejecuciones actuales.

## Navegacion

- [Protocolo](../../../../experiments/sprint2_quality/README.md)
- [Metricas y alineacion ISO](../../../../experiments/sprint2_quality/metrics.md)
- [Casos congelados](../../../../experiments/sprint2_quality/cases/)
- [Informe](Informe_Calidad_Algoritmos_ISO25023_Andro_Quispe.md)
- [Resultados crudos](results/raw_results.csv)
- [Resumen](summary_results.csv)
- [Ambiente y hashes](results/manifest.json)
- [Graficos](figures/)
- [Capturas reales](screenshots/)
- [Respuestas HTTP y correspondencia](ui/)
- [Pruebas ejecutadas](tests/)

El SHA de medicion identifica el codigo productivo evaluado. Los archivos del
experimento se incorporan despues de medir; el manifiesto de entrega conserva
sus hashes. El frontend se utiliza desde su HEAD, sin cambios de componentes.
Los identificadores de BD de la evidencia UI se remapean a un catalogo temporal;
su hash HTTP no se presenta como identico al hash del problema sintetico directo.
Se verifica igualdad de cobertura y metricas de calidad por motor. El tiempo
HTTP/UI no forma parte del resumen experimental.

## Reproduccion de pruebas

Con el entorno Python del backend y los binarios PostgreSQL locales configurados:

```bash
python -m pytest tests/unit/optimization/test_sprint2_quality.py tests/unit/optimization/test_first_fit.py tests/unit/optimization/test_best_fit.py tests/unit/optimization/test_worst_fit.py tests/unit/optimization/test_hole_filling.py tests/unit/optimization/test_best_residual_fit.py tests/unit/optimization/test_beam_search.py tests/unit/optimization/test_engine_execution.py tests/unit/optimization/test_metrics.py tests/unit/optimization/test_run_geometry.py tests/unit/optimization/test_rotation.py tests/unit/optimization/test_ta045_separation.py tests/unit/optimization/test_retazo_priority.py tests/unit/optimization/test_comparison_v2_contract.py tests/unit/optimization/test_comparison_snapshot.py tests/unit/optimization/test_experimental_cases.py -q -ra
python -m pytest tests/api/optimization/test_comparisons_v2_api.py tests/api/optimization/test_comparison_v2_preflight.py tests/integration/optimization/test_comparison_v2_persistence.py -q -ra
python -m experiments.sprint2_quality.verify_evidence docs/evidence/sprint-02/andro-calidad-algoritmos/results
```

## Captura reproducible

Iniciar `python -m tests.e2e_server` con PostgreSQL temporal, y el frontend
actual con `VITE_API_URL=http://127.0.0.1:8017` en puerto 5173. Instalar/reutilizar
Playwright y Chromium en un entorno de herramientas. Ejecutar:

Configurar `QUALITY_UI_USERNAME` y `QUALITY_UI_PASSWORD` con el usuario sintetico
del servidor de pruebas. No usar credenciales reales ni guardarlas en Git.

```bash
QUALITY_PYTHON=python QUALITY_FRONTEND_SHA=SHA_FRONTEND node experiments/sprint2_quality/capture.cjs
```

Si Playwright no esta en la resolucion de modulos, especificar
`QUALITY_PLAYWRIGHT_MODULE` con su ruta instalada. El script usa un navegador
real, no intercepta respuestas ni inyecta resultados. No confirma cortes y
comprueba que inventario no cambio durante cada comparacion. `ui_seed.py`
solo acepta el servidor temporal propio; no utiliza DATABASE_URL del entorno.

## Entrega local

La revisión de decisión se genera desde los CSV existentes, sin ejecutar de nuevo
la batería. Requiere `python-docx` y `matplotlib`. El generador comprueba los
resúmenes contra las mediciones, la validez, las tablas del Word y los hashes:

```bash
python experiments/sprint2_quality/decision_report.py
```

El Word se genera temporalmente en la raíz del repositorio para su revisión y
entrega externa; no se incluye en Git. Los gráficos anteriores se conservan como
evidencia secundaria. `export.py` corresponde a la entrega histórica y no debe
usarse para sustituir la versión revisada.
