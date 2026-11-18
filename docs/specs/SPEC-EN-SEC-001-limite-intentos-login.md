# SPEC-EN-SEC-001 — Límite de intentos de inicio de sesión y auditoría de dependencias

## Información general

| Campo | Valor |
|---|---|
| ID | EN-SEC-001 |
| Nombre | Límite de intentos de login por cuenta e IP (V-19) y auditoría de dependencias en CI (V-20) |
| Tipo | Enabler de seguridad |
| Sprint | Sprint 2 |
| Versión | 1.0 |
| Estado | **Implemented** (código, migración, pruebas, CI, escaneo ZAP local y evidencia en este PR). Pasa a Verified después de la revisión, el CI verde y el merge. |
| Responsable | Fabricio Danniel Aguilar Chavez (`fabrilar1`) |
| Reviewer | Andro Joseph Quispe Cesias (distinto del autor) |
| Ramas | `newglass-backend`: `feature/EN-SEC-001-limite-intentos-login` · `newglass-frontend`: `feature/EN-SEC-001-auditoria-dependencias` |
| Base | backend `main` en `2afa9d7` · frontend `main` en `428652b` |
| Fecha | 18/11/2026 |
| Característica ISO/IEC 25010 | Seguridad: resistencia (V-19) y revisión de dependencias (V-20) del modelo de calidad |
| Evidencia | [docs/evidence/sprint-02/EN-SEC-001](../evidence/sprint-02/EN-SEC-001/README.md) |
| Referencias | Modelo de calidad ISO 25010 v1.3 (monorepo `optimizador-vidrio`, V-19 y V-20) · SPEC-TA-QA-006 (hallazgo H-01) · [Revisión de `seguridad-owasp-zap`](../reviews/REVIEW-seguridad-owasp-zap.md) |

## 1. Objetivo

Cerrar dos vacíos de seguridad del modelo de calidad:

- **V-19:** `POST /api/auth/login` no limita los intentos fallidos. Se agrega un bloqueo
  temporal por cuenta y por IP de cliente, compartido por todos los procesos del backend.
- **V-20:** ningún workflow revisa las dependencias. Se agrega una auditoría automática en el
  CI del backend (`pip-audit`) y del frontend (`npm audit`).

## 2. Alcance

### Incluye

- Regla de límite de intentos en dominio y aplicación; almacén en PostgreSQL (tabla
  `intentos_login`, migración `s2sec01a1b2c`); el router solo traduce a HTTP.
- Respuesta 429 con `Retry-After` y un mensaje genérico único.
- Tres variables de entorno para umbral, ventana y bloqueo, documentadas en `.env.example`.
- Pruebas unitarias, de API y de integración con PostgreSQL (estas últimas corren en el CI).
- Paso `pip-audit` en el CI del backend y paso `npm audit --omit=dev --audit-level=high` en el
  CI del frontend.
- Revisión de la rama `seguridad-owasp-zap` (documento aparte, ver Referencias).

### Fuera de alcance

- Cambiar el `Procfile` o la configuración de Heroku: solo se propone (D-07, P-01).
- Otras actualizaciones de dependencias distintas de python-multipart (§4.3, D-14).
- CAPTCHA, segundo factor, avisos por correo o desbloqueo manual por un administrador.
- La diferencia de tiempo de respuesta entre una cuenta inexistente (sin bcrypt) y una
  contraseña errónea, que existía antes de esta tarea (P-05).
- Cambios en el frontend fuera del workflow de CI. El mensaje 429 llega en `detail`, igual
  que los demás errores de login, y la pantalla actual ya muestra `detail`.

## 3. Actor y precondiciones

**Actor:** cualquier persona que intenta iniciar sesión (con o sin cuenta).

**Precondiciones:** migración `s2sec01a1b2c` aplicada; para limitar por IP real detrás de
Heroku, Uvicorn debe confiar en el router (P-01).

## 4. Situación actual (auditoría en `main` `2afa9d7`)

### 4.1 Login sin límite (V-19)

- `app/modules/authentication/presentation/router.py`: cada intento verifica la contraseña;
  no hay contador ni bloqueo. Mitigaciones previas: mensaje único, bcrypt y validación del
  formato del usuario.
- El plan del equipo prevé varios procesos web y workers: un contador en memoria no se
  compartiría entre ellos.

### 4.2 Proxy de Heroku y la IP del cliente

- `Procfile`: `python -m uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`, sin
  `--forwarded-allow-ips`.
- Uvicorn 0.54 activa *proxy headers* por defecto, pero solo confía en `127.0.0.1,::1`
  (`FORWARDED_ALLOW_IPS`). Detrás del router de Heroku, `request.client.host` es la IP interna
  del router, la misma para muchos clientes.
- Con `--forwarded-allow-ips="*"`, Uvicorn toma la **primera** IP de `X-Forwarded-For`, que el
  cliente puede inventar (`uvicorn/middleware/proxy_headers.py`, `always_trust`). Con una lista
  de redes confiables, recorre la cabecera desde la derecha y toma la primera IP no confiable:
  la que agregó el router.

### 4.3 Dependencias (V-20)

| Repo | Comando | Resultado en `main` |
|---|---|---|
| backend | `pip-audit -r requirements.txt` (2.10.1) | **12 avisos (6 distintos)** en `python-multipart==0.0.20`: PYSEC-2026-1852, -3036, -3037, -3038, -3039, -3040 |
| frontend | `npm audit --omit=dev --audit-level=high` | 0 vulnerabilidades |
| frontend | `npm audit` (incluye desarrollo) | 10 (3 moderadas, 7 altas), todas de desarrollo |

Esta rama sube python-multipart a 0.0.31 (D-14): `pip-audit` pasa a 0 avisos y el paso nuevo
del CI queda en verde. La rama `seguridad-owasp-zap` trae el mismo cambio y debe quitarlo al
rehacerse.

## 5. Reglas

- **RN-01:** cada intento usa dos claves: la cuenta (usuario normalizado por `Username.parse`:
  sin espacios y en mayúsculas) y la IP del cliente. Una cuenta inexistente también tiene clave.
- **RN-02:** una clave acumula fallos en una ventana fija que empieza con su primer fallo. Si la
  ventana venció, el siguiente fallo abre una nueva y el contador vuelve a 1.
- **RN-03:** al llegar a `LOGIN_MAX_FAILED_ATTEMPTS` fallos (por defecto 5) dentro de
  `LOGIN_ATTEMPT_WINDOW_MINUTES` (15), la clave queda bloqueada `LOGIN_LOCKOUT_MINUTES` (15) y
  su contador vuelve a 0. Al vencer el bloqueo hay otra vez 5 intentos.
- **RN-04:** si cualquiera de las dos claves está bloqueada, la respuesta es **HTTP 429** con
  `Retry-After` (segundos que faltan, redondeados hacia arriba) y el mensaje
  "Demasiados intentos fallidos. Espera unos minutos antes de volver a intentarlo.". El
  código, el cuerpo y la cabecera no dependen de si la cuenta existe ni de qué clave bloqueó.
- **RN-05:** durante el bloqueo no se verifica la contraseña, ni siquiera si es la correcta.
- **RN-06:** un login exitoso borra los intentos de la cuenta. No borra los de la IP.
- **RN-07:** las tres variables son enteros ≥ 1; un valor inválido produce un error que nombra
  la variable, sin mostrar su valor.

## 6. Decisiones y preguntas abiertas

### Decisiones

| ID | Decisión | Motivo |
|---|---|---|
| D-01 | Bloqueo por cuenta y por IP (RN-01). | Decisión del equipo. La cuenta frena la fuerza bruta contra un usuario aunque cambie la IP; la IP frena el rociado de contraseñas contra muchos usuarios. |
| D-02 | 5 fallos / 15 min / 15 min, configurables con `LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_ATTEMPT_WINDOW_MINUTES` y `LOGIN_LOCKOUT_MINUTES`. | Decisión del equipo. Se leen en cada petición, como `TOKEN_MINUTOS`. |
| D-03 | 429 + `Retry-After` + mensaje genérico idéntico (RN-04). | Decisión del equipo: no revelar si la cuenta existe. |
| D-04 | El fallo que alcanza el umbral ya responde 429 (no 401). | La persona sabe enseguida que debe esperar, y el resultado es el mismo para cuenta existente o inexistente. |
| D-05 | Una cuenta desactivada con la contraseña correcta (403) no suma. | No es un intento de adivinar la contraseña; ya se sabe que es correcta. |
| D-06 | El éxito limpia solo la cuenta (RN-06). | Si limpiara la IP, alguien con una cuenta propia podría reiniciar el contador de su IP entre intento e intento contra otras cuentas. |
| D-07 | La IP es `request.client.host`, la que entrega Uvicorn con *proxy headers*. La aplicación no lee `X-Forwarded-For` por su cuenta. | Decisión del equipo. Así el cliente no elige su IP con una cabecera; la confianza en el proxy se configura en un solo lugar (Uvicorn). Para Heroku se propone un cambio de `Procfile` (P-01), no aplicado. |
| D-08 | Persistencia en PostgreSQL: una fila por clave en `intentos_login`. Escritura atómica con `INSERT ... ON CONFLICT DO NOTHING` + `SELECT ... FOR UPDATE`. | Decisión del equipo (varios procesos). El bloqueo de fila evita perder fallos cuando dos procesos fallan a la vez con la misma clave. |
| D-09 | Ventana fija desde el primer fallo, no ventana deslizante. | Una sola fila y una sola actualización por clave; el resultado es el mismo para la decisión 5/15/15. |
| D-10 | Un usuario con formato inválido (422) no cuenta. | Se rechaza antes de consultar la base y no verifica ninguna contraseña (regla previa de HU-001). |
| D-11 | Limpieza oportunista: cada fallo borra filas sin actividad hace más de ventana + bloqueo y sin bloqueo vigente. | Evita que la tabla crezca con usuarios inexistentes sin agregar tareas programadas. Usa el índice `ix_intentos_login_actualizado_en`. |
| D-12 | Las escrituras del almacén confirman su propia transacción en la sesión de la petición. | En el login, antes de escribir solo hubo lecturas; así el contador queda guardado aunque la respuesta sea un error. |
| D-13 | Auditoría de dependencias como último paso de cada job de CI, con `if: !cancelled()`. Backend: `pip-audit==2.10.1 --requirement requirements.txt`. Frontend: `npm audit --omit=dev --audit-level=high`. | Las pruebas corren y se informan aunque la auditoría falle, y el job falla si hay avisos. En el frontend se audita lo que llega al bundle (dependencias de producción). |
| D-14 | Esta rama actualiza `python-multipart` de 0.0.20 a 0.0.31 en `requirements.txt`. | Decisión del responsable (18/11/2026): sin ella el paso de `pip-audit` deja el CI en rojo. El cambio se comprobó sin diferencias en `/api/orders/import/preview` (CSV, XLSX, nombre con tildes, 1 MB, > 1 MB y sin archivo) y la suite queda en verde. La rama `seguridad-owasp-zap` debe quitar ese mismo cambio al rehacerse. |

**Por qué se agrega `pip-audit` (D-13):** el backend no tenía ninguna herramienta de
auditoría de dependencias (V-20) y `pip` no trae una. `pip-audit` lo mantiene la PyPA, consulta
las bases públicas de avisos (PyPI/OSV), resuelve `requirements.txt` en un entorno propio sin
tocar el del proyecto y devuelve código ≠ 0 si encuentra vulnerabilidades. Solo se instala en
el CI, con versión fija; no es una dependencia de la aplicación ni un servicio nuevo. En el
frontend no se agrega nada: `npm audit` viene con npm.

### Preguntas abiertas

| ID | Pregunta | Estado |
|---|---|---|
| P-01 | **Procfile en Heroku.** Sin cambios, todos los clientes comparten la IP del router y 5 fallos de cualquiera bloquearían el login por IP de muchos usuarios. Propuesta: agregar `--forwarded-allow-ips="10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"` (o la variable `FORWARDED_ALLOW_IPS` con el mismo valor) al comando de Uvicorn. **No usar `"*"`** (§4.2). Hay que confirmar en Staging que la IP de conexión del router está en esos rangos. | Abierta. Se decide en el PR, **antes de desplegar**. |
| P-02 | Red compartida: si todo el taller sale por una sola IP pública, 5 fallos de cualquier persona bloquean el login de todos durante 15 min. | Abierta. Se puede subir el umbral con la variable de entorno; un umbral distinto para IP sería otra tarea. |
| P-03 | Quien conoce un usuario puede bloquear esa cuenta a propósito. | Aceptado: es propio del bloqueo por cuenta; el bloqueo es temporal. |
| P-04 | La tarea menciona "la matriz de 59 rutas" de TA-QA-006. En `main` la matriz ya tiene **61** rutas (EN-UX-004 agregó las de cancelación). | Se verifica la matriz vigente de 61 rutas (CA-11). |
| P-05 | Una cuenta inexistente responde sin calcular bcrypt (diferencia de tiempo). | Fuera de alcance; anterior a esta tarea. |
| P-06 | El escaneo ZAP encontró que `GET/PATCH /api/users/{id_usuario}` responde 500 con un id mayor que 2 147 483 647 (falta `le=2147483647`, módulo `users`), y que el frontend publicado no envía CSP ni anti-clickjacking. | Fuera de alcance (otro módulo / GitHub Pages). Se proponen como tareas aparte. |

## 7. Diseño técnico

| Archivo | Capa | Cambio |
|---|---|---|
| `app/modules/authentication/domain/login_attempts.py` | Domain | Nuevo. `AttemptKey`, `AttemptPolicy`, `AttemptState`, `register_failure` y `seconds_locked` (RN-02, RN-03). Sin dependencias externas. |
| `app/modules/authentication/domain/errors.py` | Domain | `TooManyLoginAttemptsError(retry_after_seconds)`. |
| `app/modules/authentication/application/ports.py` | Application | Contrato `LoginAttemptStore` (`get`, `update` atómico, `clear`, `purge`). |
| `app/modules/authentication/application/use_cases.py` | Application | `authenticate_with_attempt_limit`: consulta bloqueos, delega en `authenticate`, registra fallos y limpia la cuenta tras el éxito (RN-04 a RN-06). |
| `app/modules/authentication/infrastructure/sqlalchemy_login_attempt_store.py` | Infrastructure | Nuevo. Implementa el contrato con `ON CONFLICT DO NOTHING` + `FOR UPDATE` (`populate_existing` para releer la fila). |
| `app/models.py` | Infrastructure | Modelo `IntentoLogin` (tabla `intentos_login`). |
| `alembic/versions/s2sec01a1b2c_en_sec_001_intentos_login.py` | Migración | Crea la tabla, sus restricciones y el índice; `down_revision = "s2cmp02d4e6f8"`. Downgrade: borra la tabla (solo contadores temporales). |
| `app/shared/config/__init__.py` | Shared | `login_attempt_limits()` con validación (RN-07). |
| `app/modules/authentication/presentation/dependencies.py` | Presentation | `get_login_attempt_store`, `get_login_attempt_policy`, `get_clock`, `get_client_ip`. |
| `app/modules/authentication/presentation/router.py` | Presentation | El login delega en el caso de uso y traduce `TooManyLoginAttemptsError` a 429 + `Retry-After`. Documenta 429 en OpenAPI. |
| `.env.example` | Configuración | Las tres variables, comentadas con sus valores por defecto. |
| `.github/workflows/ci.yml` (backend y frontend) | CI | Paso de auditoría (D-13). |
| `tests/conftest.py` | Pruebas | La base SQLite de pruebas incluye `intentos_login`. |
| `tests/integration/...` (4 archivos) y `tests/unit/test_database_structure.py` | Pruebas | Ajuste mínimo a la nueva cabeza `s2sec01a1b2c` y a la tabla nueva (ver §9). |

**Tabla `intentos_login`:** `alcance` VARCHAR(10) (`account` o `ip`), `clave` VARCHAR(64),
`fallos` INTEGER ≥ 0, `inicio_ventana` y `bloqueado_hasta` TIMESTAMPTZ nulos,
`actualizado_en` TIMESTAMPTZ. PK (`alcance`, `clave`); índice por `actualizado_en`.

**Orden de integración de migraciones:** al 18/11/2026 ninguna rama remota abierta de
`newglass-backend` agrega migraciones (revisadas `fix/fase-1b2-rectangulo-recuperable`,
`feat/HU-MAT-001-trazabilidad-material`, `feature/EN-UX-004-cancel-supervisor`,
`qa/TA-QA-002-rendimiento-carga`, `qa/TA-QA-009-seguridad-operacional-corte`,
`docs/HU-VENT-001-analisis-plancha-completa`, `test/S2-andro-calidad-algoritmos` y
`seguridad-owasp-zap`). Si otra rama crea una migración sobre `s2cmp02d4e6f8` antes de este
merge, la que se integre segunda debe cambiar su `down_revision` a la cabeza de la primera y
actualizar las pruebas que fijan la cabeza (§9).

**Aplicación en Staging (fuera de este PR):** `alembic upgrade head` en Staging solo con OK
del equipo, después del merge y antes de redesplegar.

## 8. Criterios de aceptación

| ID | Criterio | Tarea | Verificación |
|---|---|---|---|
| CA-01 | 5 fallos en 15 min responden 401, 401, 401, 401 y 429 con `Retry-After: 900`. | V-19 | `test_fifth_failure_locks_with_429_and_retry_after` |
| CA-02 | Durante el bloqueo, la contraseña correcta recibe 429 sin token y sin verificarse. | V-19 | `test_locked_account_rejects_the_right_password`, `test_during_the_lock_even_the_right_password_is_rejected_without_checking_it` |
| CA-03 | Al vencer el bloqueo, el login correcto funciona. | V-19 | `test_lock_ends_when_the_lockout_expires` |
| CA-04 | Fallos fuera de la ventana no se acumulan. | V-19 | `test_failures_outside_the_window_do_not_accumulate` |
| CA-05 | Un login exitoso reinicia el contador de la cuenta. | V-19 | `test_successful_login_resets_the_account_counter` |
| CA-06 | Cuenta inexistente: mismos códigos, cuerpo y `Retry-After` que una existente. | V-19 | `test_missing_account_gets_identical_responses`, `test_locked_response_body_and_headers_do_not_reveal_the_account` |
| CA-07 | El bloqueo por IP afecta a otras cuentas desde esa IP; el de cuenta sigue en otras IP; `X-Forwarded-For` del cliente se ignora. | V-19 | `test_ip_lock_blocks_another_valid_account_from_the_same_ip`, `test_account_lock_follows_the_account_across_ips`, `test_x_forwarded_for_sent_by_the_client_is_ignored` |
| CA-08 | Umbral, ventana y bloqueo se configuran por entorno y están en `.env.example`. | V-19 | `test_limits_are_configurable_by_environment`, `test_login_attempt_config.py` |
| CA-09 | El estado vive en PostgreSQL con migración; los fallos concurrentes no se pierden. | V-19 | `test_en_sec_001_login_attempts_postgres.py` (CI), DDL offline idéntico al ORM (evidencia) |
| CA-10 | Dominio y aplicación no importan frameworks ni infraestructura. | V-19 | `tests/unit/authentication/test_layer_dependencies.py` |
| CA-11 | La matriz de TA-QA-006 (61 rutas protegidas en `main`) sigue pasando y el login sigue siendo público. | V-19 | `test_security_role_matrix.py`, `test_login_stays_public_and_documents_429` |
| CA-12 | El CI falla con vulnerabilidades conocidas: `pip-audit` (backend) y `npm audit --omit=dev --audit-level=high` (frontend). | V-20 | Ejecución local de ambos comandos (evidencia) |
| CA-13 | Escaneo OWASP ZAP local (baseline del frontend y la API, y autenticado de la API) sin alertas altas. | V-19/V-20 | ZAP 2.17.0: 0 High en los tres escaneos; hallazgos Low/Medium registrados (evidencia §6, P-06). |

## 9. Pruebas

| Archivo | Tipo | Qué cubre |
|---|---|---|
| `tests/unit/authentication/test_login_attempts_policy.py` | Unitaria (dominio) | Umbral, ventana, bloqueo, redondeo de `Retry-After`, fallos concurrentes sobre un estado bloqueado, validaciones. |
| `tests/unit/authentication/test_login_attempt_use_case.py` | Unitaria (aplicación) | Secuencias 401/429, cuenta inexistente, cuenta desactivada, IP y cuenta, sin IP, limpieza. |
| `tests/unit/authentication/test_login_attempt_store.py` | Infraestructura (SQLite) | Persistencia, alcances independientes, limpieza; **regresión**: `update` relee la fila aunque la sesión tenga una copia vieja (falla sin `populate_existing`). |
| `tests/unit/shared/test_login_attempt_config.py` | Unitaria | Valores por defecto, valores del entorno y valores inválidos. |
| `tests/api/test_login_attempt_limit_api.py` | API | CA-01 a CA-08 y CA-11 de extremo a extremo, con reloj e IP sustituidos. |
| `tests/integration/test_en_sec_001_login_attempts_postgres.py` | Integración (PostgreSQL, CI) | Migración = ORM, downgrade, restricciones CHECK y 8 sesiones concurrentes sin perder fallos. |

**Pruebas existentes ajustadas (cambio mínimo, se avisa en el PR):**
`tests/conftest.py` (tabla en la base SQLite), `tests/unit/test_database_structure.py` (lista de
tablas), `tests/integration/test_staging_bootstrap.py` (cabeza y largo de la cadena: 16),
`tests/integration/test_clients_migration.py` y `tests/integration/inventory/test_repository.py`
(constante `HEAD`), `tests/integration/optimization/test_comparison_v2_migration.py` (compara
el esquema en `s2cmp02d4e6f8` y el ORM vigente en `head`).

## 10. Evidencia requerida

`docs/evidence/sprint-02/EN-SEC-001/README.md`: salidas en texto de pytest (antes y después),
de las pruebas nuevas, del DDL offline y de la verificación sobre PostgreSQL local, de
`pip-audit` y `npm audit`, del frontend, y los reportes y el resumen del escaneo ZAP con la
comparación con el informe de la rama `seguridad-owasp-zap`.

## Historial de estado

| Fecha | Versión | Estado | Cambio |
|---|---|---|---|
| 18/11/2026 | 1.0 | Implemented | SPEC, código, migración `s2sec01a1b2c`, pruebas, pasos de auditoría en CI, escaneo OWASP ZAP local y evidencia. |
