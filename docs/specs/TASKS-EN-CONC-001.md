# EN-CONC-001 / Tareas de Sprint 2

| Tarea | Evidencia esperada |
|---|---|
| Admisión durable | Dos actores admitidos, cupo controlado, reserva única por pedido y reintentos idempotentes. |
| Despacho y permisos | FIFO, un supervisor activo y revalidación de `optimization.execute` al reclamar. |
| Aislamiento y estado HTTP | Administrador, Operario y Almacenero consultan solo lo permitido por sus permisos efectivos y su propiedad. |
| Cancelación y recuperación | Cancelar en espera conserva el cálculo ajeno. Reiniciar conserva la cola. Procesos sin cierre acreditado retienen la plaza. |
| Integridad transaccional | La simulación conserva el inventario. La confirmación V4 protege el stock frente a dos reclamos. |

## Fase 2B.1

| Tarea | Criterio verificable |
|---|---|
| Migración operativa | PostgreSQL rechaza dos plazas activas. La activación importa únicamente trabajos 2A sin dueño y revierte ante un registro ambiguo. |
| Servicio separado | Las dos APIs admiten y consultan. Dos reclamantes compiten. El dueño conserva su proceso durante el reinicio de la API. |
| Fallos y cancelación | Se prueba la caída del worker, la pérdida de conexión, un token obsoleto, la revocación en espera y ambas formas de cancelación. |
| Regresión y recursos | Se conservan privacidad, V3/V4 y rollback de confirmación. Se registran RSS agregado, CPU, tiempo y conexiones del worker. |
| Operación del piloto | Quedan documentados el arranque WSL2, el modo FF/V1 y el bloqueo de recuperación. |

## Fase 2B.2

| Tarea | Criterio verificable |
|---|---|
| Fencing | Token o generación anteriores no pueden renovar, escribir ni cerrar. Cada reclamo registra un intento nuevo. |
| Recuperación | Caídas reales antes de spawn y después de un join acreditado. La expiración con propietario vivo retiene la plaza. Un hijo sin join acreditado deja el trabajo bloqueado. |
| Exclusión V1/V2 | Dos APIs y dos candidatos comprueban ambos sentidos de exclusión y un solo reclamo. FF devuelve un rechazo explícito en el piloto. |
| Privacidad y compatibilidad | Aislamiento de propietario, regresión V3/V4, migración de pendientes y propietarios 2B.1, confirmación V4 y rollback. |
