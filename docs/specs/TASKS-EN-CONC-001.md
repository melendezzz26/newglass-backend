# EN-CONC-001 / Tareas de Sprint 2

| Tarea | Evidencia esperada |
|---|---|
| Admisión durable | Dos actores admitidos, cupo controlado, reserva única por pedido y reintentos idempotentes. |
| Despacho y permisos | FIFO, un supervisor activo y revalidación de `optimization.execute` al reclamar. |
| Aislamiento y estado HTTP | Administrador, Operario y Almacenero consultan solo lo permitido por sus permisos efectivos y su propiedad. |
| Cancelación y recuperación | Cancelar en espera conserva el cálculo ajeno. Reiniciar conserva la cola. Procesos sin cierre acreditado retienen la plaza. |
| Integridad transaccional | La simulación conserva el inventario. La confirmación V4 protege el stock frente a dos reclamos. |
