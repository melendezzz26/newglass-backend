# Registro de pruebas locales

Entorno WSL2, Python 3.11.17, PostgreSQL 16 temporal para integración. Sin
conexión a producción ni confirmación de cortes.

- Selección unitaria de calidad, motores, geometría, métricas, V2 y nuevos
  tests: **531 passed**. Incluye los tests nuevos; no se deben volver a sumar.
- `tests/integration/optimization/test_scale_multiprocess_admission.py`:
  **3 passed**. Dos casos de rechazo en el mismo proceso y un caso de dos
  coordinadores en procesos independientes con PostgreSQL temporal compartido.
- `test_common_capture_independent_commits_external_join_and_no_consumption`:
  **1 passed** en la selección de integración previa. Los tests de dos procesos
  se volvieron a ejecutar al añadir los casos de mismo proceso; no se suman dos
  veces.
- Regresión de API V2, preflight y persistencia en PostgreSQL temporal:
  **88 passed**, con una advertencia de deprecación de Starlette/TestClient.
- Piloto: 24 observaciones, todas completas y validadas. Calentamiento: 24
  observaciones separadas. Medición: 120 observaciones, todas completas y
  validadas. Esto no es una cuenta de tests de pytest.

El test integrado de dos coordinadores no es una prueba HTTP de dos servidores
ni mide el CPU o RSS agregado. No demuestra recuperación o cancelación entre
procesos. Ver el informe para los límites y trabajo pendiente.
