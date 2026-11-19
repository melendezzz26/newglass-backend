# Contrato HTTP de FF durable

## Admitir el cálculo

`POST /api/optimization/runs/durable` requiere `optimization.execute`, una cabecera `Idempotency-Key` de 8 a 64 caracteres ASCII (`A-Z`, `a-z`, `0-9`, `_`, `-`) y el cuerpo `{"order_ids":[123]}`. Devuelve `202 Accepted` con la cabecera `Location: /api/optimization/runs/durable/456`. El cuerpo inicial tiene este formato.

```json
{"id_optimizacion":456,"state":"WAITING","result_url":null}
```

Una repetición del mismo lote con la misma clave y propietario devuelve el mismo identificador y su estado actual. El orden de la lista es indiferente. Otra selección con la misma clave devuelve `409 STATE_CONFLICT`. Cada propietario tiene su propio espacio de claves de FF. El cupo compartido de espera lleno devuelve `503 OPTIMIZER_BUSY` con `Retry-After` y `request_accepted: false`. El modo mixto es necesario para admitir una nueva solicitud. Una clave ausente o inválida devuelve `422 INVALID_INPUT`.

## Consultar y reconectar

`GET /api/optimization/runs/durable/{id}` exige `optimization.view` y propiedad. Devuelve `WAITING`, `RUNNING`, `RECOVERY_BLOCKED` o `TERMINAL`. Un identificador ajeno devuelve `404`, incluso para Administrador. Una solicitud terminal enlaza al resultado existente `GET /api/optimization/runs/{id}`. Ese resultado distingue `COMPLETADA`, `SIN_SOLUCION` y `FALLIDA`. La confirmación conserva `POST /api/optimization/runs/{id}/confirm`, propiedad, `optimization.confirm` y las verificaciones transaccionales sobre el resultado y el stock.

El frontend del piloto debe generar y conservar la clave antes de enviar el `POST`. Al perder la respuesta puede repetir exactamente la operación con la misma clave. Una vez recibido `Location`, puede retomar la consulta desde cualquier API. Debe mostrar `RECOVERY_BLOCKED` como recuperación pendiente y conservar el identificador. Los estados de cola son el único progreso distribuido disponible en FF. El consumo de NDJSON histórico se conserva en modo legacy. En el piloto, el frontend debe utilizar la ruta durable para iniciar FF. La API nueva no ejecuta cálculos al consultar estado o resultado.
