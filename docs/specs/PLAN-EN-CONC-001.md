# EN-CONC-001 / Plan técnico

1. Extender la persistencia V2 con la marca de cola y guardar la reserva con la admisión. Proteger cupo e idempotencia con PostgreSQL. Mantener intactos los resultados V3/V4.
2. Añadir un despachador al ciclo de vida de la API y reutilizar el supervisor de seis motores. Verificar permiso y propiedad en el reclamo. Separar la espera de la recuperación de trabajos reclamados.
3. Mantener el estado consultable e integrar la cancelación de espera con la transacción de pedidos. Preservar el rechazo de cancelación ajena y el cierre de los procesos activos.
4. Verificar permisos de tres roles, admisión competitiva, idempotencia, revocación, reinicio, cancelación, recuperación y confirmación transaccional con PostgreSQL local desechable.

## Fase 2B.1

1. Crear las tablas operativas y validar la migración desde `s2sec01a1b2c` en PostgreSQL desechable.
2. Incorporar la política por base de datos, la adopción de pendientes 2A y la plaza global única. Mantener en el agregado comercial los resultados, la reserva, la selección y los recibos.
3. Separar el servicio de cálculo del ciclo de vida FastAPI. Reutilizar el supervisor secuencial y acreditar el cierre antes de liberar la plaza. Conservar el bloqueo ante pérdida de evidencia.
4. Verificar dos APIs y dos reclamantes, reinicios, fallos de conexión, RBAC, rechazo legacy en el piloto y regresiones V3/V4.
5. Repetir las tandas del CI y documentar el arranque local, el cambio de modo y la evidencia de recursos.

## Fase 2B.2

1. Agregar generación e historial de intentos y ensayar la migración de filas 2B.1 en PostgreSQL desechable.
2. Proteger cada escritura operativa con el propietario y la generación. Separar la expiración del heartbeat de la evidencia de cierre.
3. Incorporar al piloto mixto la admisión V1 en la misma transacción que la reserva y un único índice de plaza. Mantener cerrado FF en el piloto y verificar el rechazo de lecturas ajenas.
4. Ejecutar los escenarios de procesos reales, caída antes y después de un resultado, restitución de conectividad, cancelación y concurrencia V1/V2. Ejecutar regresión V3/V4 y CI del SHA publicado.
