# TA-047 — Evidencia de preflight

Base `7a996631046b3769fe78db384b077cd9e75049cb`; repositorio `newglass-backend`, arquitectura cliente-servidor. La evidencia histórica de la implementación v2 previa está en [TA-047 legado](https://github.com/melendezzz26/optimizador-vidrio/blob/integration-02/docs/evidence/sprint-02/TA-047/README.md).

## RED y cambio de contrato

La base validaba geometría pero aceptaba sin stock o sin encaje individual, ejecutando los seis motores y produciendo `SIN_SOLUCION`. Los dos casos unitarios originales se ejecutaron separadamente contra la base: RED-01 sin stock y RED-02 pieza 4x2 en fuente 1x1; ambos fallaron porque no se lanzó `RunError`. La función específica de viabilidad ahora pasa ambos casos.

## Orden y seguridad

Una consulta no mutante comprueba capacidad; no adquiere un slot. Se captura un snapshot y se valida geometría y encaje individual antes de admisión; al adquirir capacidad se verifica de nuevo atómicamente. La aceptación lee un snapshot nuevo y repite la prueba antes de crear comparación/run y cambiar pedidos. Pruebas HTTP/PostgreSQL registran ausencia de escrituras, ausencia de adquisición ante rechazo, ausencia de workers y liberación de admisión. Una prueba cambia stock entre la primera lectura y la adquisición: la segunda comprobación rechaza sin crear ejecución ni alterar pedidos. No se reserva stock para cálculo; los controles existentes de confirmación manejan cambios posteriores.

Para una pieza aislada se usa el layout y las orientaciones del motor. El margen aplica al borde y la separación solo a pares de piezas; no se garantiza que el conjunto completo quepa. El trabajo está limitado a 200000 checkpoints y a la memoria raster existente, con una comprobación simultánea por proceso. Un límite agotado devuelve 503, no una falsa imposibilidad.

## HTTP

- Pedido vacío: 422 `INVALID_INPUT`.
- Sin material compatible: 422 `NO_COMPATIBLE_STOCK`.
- Sin colocación individual: 422 `PIECE_DOES_NOT_FIT`.
- Límite de comprobación o preparación no segura: 503 `RESOURCE_UNAVAILABLE`.

Todos usan `detail.code` y `detail.message`; el rechazo previo en comparación incluye `request_accepted: false`. Los mensajes no contienen datos internos.

## Validaciones locales

- Windows Python 3.11: placement, TA-045 y motores, preflight y casos geométricos: 363 PASS; repetición focalizada de admisión y preflight: 37 PASS. Son selecciones solapadas, no deben sumarse.
- Linux/WSL Python 3.11, PostgreSQL 16 temporal: regresión de concurrencia, run, comparación, confirmación y supervisor: 129 PASS, sin omisiones, 1 aviso de dependencia Starlette/httpx.
- Ejecutores Linux aislados: sequential runner 26 PASS, comparison runner 14 PASS y comparison calculation 8 PASS.
- Una ejecución anterior en Windows de los ejecutores sensibles a procesos falló; se repitieron en Linux como exige el CI. Una regresión API amplia detectó diferencia de precedencia de ocupación; se añadió consulta de capacidad sin reserva y la selección focalizada posterior incluye el caso.
- Frontend: prueba RED de pérdida de referencia tras rechazo, GREEN con recuperación persistida; ver la evidencia del PR frontend coordinado.

Los resultados no sustituyen al CI. La compatibilidad post-merge y el humo aún no se han ejecutado.
- Repeticion de pruebas de temporizacion del worker: 5 PASS. Limites, estres, recuperacion, runs y visualizacion: 63 PASS, 1 aviso Starlette/httpx. Una pasada previa fallo porque su fixture no contemplaba la cantidad reducida; se corrigio la expectativa conservando la comprobacion de stock sin mutacion y se repitio.
- Regresion adicional de configuracion angular, admision y preflight: 49 PASS. El fixture unitario de configuracion se actualizo para proporcionar stock compatible real al nuevo preflight.
- Regresion de limites de carga y configuracion, admision y factibilidad: 50 PASS. Los tests de limites aislan la verificacion geometrica para seguir probando exclusivamente que los presupuestos se comprueban antes de expansion; los tests reales de factibilidad y PostgreSQL se ejecutan aparte.
- Regresion Linux: 1046 pruebas unitarias de optimizacion PASS (ejecutores de proceso en sus suites separadas ya indicadas). Persistencia, publicacion de configuracion, recuperacion y concurrencia: 80 PASS, 1 aviso Starlette/httpx. Los fixtures de no-solucion usan piezas viables individualmente y capacidad global insuficiente; el fixture de publicacion conserva el margen y usa una plancha suficientemente grande.
