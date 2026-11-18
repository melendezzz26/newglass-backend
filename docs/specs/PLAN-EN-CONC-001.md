# EN-CONC-001 / Plan técnico

1. Extender la persistencia V2 con la marca de cola y guardar la reserva con la admisión. Proteger cupo e idempotencia con PostgreSQL. Mantener intactos los resultados V3/V4.
2. Añadir un despachador al ciclo de vida de la API y reutilizar el supervisor de seis motores. Verificar permiso y propiedad en el reclamo. Separar la espera de la recuperación de trabajos reclamados.
3. Mantener el estado consultable e integrar la cancelación de espera con la transacción de pedidos. Preservar el rechazo de cancelación ajena y el cierre de los procesos activos.
4. Verificar permisos de tres roles, admisión competitiva, idempotencia, revocación, reinicio, cancelación, recuperación y confirmación transaccional con PostgreSQL local desechable.
