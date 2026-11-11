# SPEC TA-047 — Preflight geométrico

Estado: en validación. Base backend `7a996631046b3769fe78db384b077cd9e75049cb`. Propietario principal: `newglass-backend`; el frontend conserva la referencia del resultado anterior cuando se rechaza una nueva solicitud.

Una selección sin piezas se rechaza con `422 INVALID_INPUT`; una sin fuente compatible, con `422 NO_COMPATIBLE_STOCK`; una pieza sin colocación individual en ninguna fuente compatible, con `422 PIECE_DOES_NOT_FIT`. La respuesta conserva `detail.code` y `detail.message`; comparación añade `request_accepted: false` cuando el rechazo ocurre antes de aceptación. El agotamiento del límite de trabajo, ocupación del preflight o incapacidad de preparar máscaras produce `503 RESOURCE_UNAVAILABLE`, nunca una conclusión falsa de no encaje.

El preflight usa las máscaras de material, la erosión por margen, la política de rotación y búsqueda de posiciones compartidas por los motores. La separación de corte es entre piezas; no exige reserva exterior en una colocación individual. La prueba de cada pieza en una fuente vacía establece **viabilidad individual mínima**, no garantiza la solución conjunta del lote. Una comparación con piezas individualmente viables puede terminar `SIN_SOLUCION` por capacidad global.

El límite existente de memoria raster y dimensiones se mantiene; se limita el trabajo a 200000 checkpoints por consulta y se serializa la comprobación en el proceso. Si se supera el presupuesto, no se califica la geometría como imposible. El sistema sigue requiriendo un solo proceso de aplicación para la admisión local.

Una lectura previa de capacidad no reserva ningún slot; después se carga y valida un snapshot sin escrituras. Solo al pasar se adquiere admisión, que vuelve a comprobar capacidad atómicamente. La aceptación obtiene un snapshot fresco y repite la validación antes de crear ejecuciones y cambiar pedidos. La comprobación no reserva stock: el inventario puede cambiar posteriormente y la confirmación mantiene sus controles de conflicto. El resultado anterior y sus eventos se conservan ante un rechazo.
