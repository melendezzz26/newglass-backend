# Evidencia — EN-SEC-001 Límite de intentos de login y auditoría de dependencias

| Campo | Valor |
|---|---|
| SPEC | [SPEC-EN-SEC-001](../../../specs/SPEC-EN-SEC-001-limite-intentos-login.md) |
| Ramas | backend `feature/EN-SEC-001-limite-intentos-login` (base `main` `2afa9d7`) · frontend `feature/EN-SEC-001-auditoria-dependencias` (base `main` `428652b`) |
| Fecha | 18/11/2026 |
| Entorno local | Windows 11, Python 3.11.9, Node 22.20.0, OWASP ZAP 2.17.0 (imagen Docker `zaproxy/zap-stable`), PostgreSQL 16 en contenedor efímero en `127.0.0.1` |
| Bases usadas | Solo bases locales desechables (SQLite de pruebas y el contenedor PostgreSQL, borrado al terminar). No se usó Staging ni Heroku. |

Todos los archivos de esta carpeta son salidas en texto o reportes generados; no contienen
secretos, tokens, usuarios reales ni datos de conexión (las únicas direcciones son de loopback).

## 1. Antes y después

| | Antes (`main`) | Después (esta rama) |
|---|---|---|
| Login con contraseña errónea repetida | 401 indefinidamente | 401 × 4 y 429 al quinto fallo, con `Retry-After: 900` |
| Contraseña correcta durante el bloqueo | — | 429, sin token y sin verificar la contraseña |
| Cuenta inexistente | 401 | Mismos códigos, cuerpo y `Retry-After` que una cuenta existente |
| Estado entre procesos | — | Tabla `intentos_login` en PostgreSQL (migración `s2sec01a1b2c`) |
| Auditoría de dependencias en CI | Ninguna | `pip-audit` (backend) y `npm audit --omit=dev --audit-level=high` (frontend) |

## 2. Pruebas backend

| Archivo | Contenido | Resultado |
|---|---|---|
| [pytest-antes-main.txt](pytest-antes-main.txt) | Suite del CI en `main` | 2748 passed, 955 skipped, 0 failed |
| [pytest-despues-en-sec-001.txt](pytest-despues-en-sec-001.txt) | Suite del CI en la rama | 2818 passed, 959 skipped, 0 failed |
| [pytest-pruebas-nuevas.txt](pytest-pruebas-nuevas.txt) | Pruebas nuevas una por una (`-v`) | 73 passed, 4 skipped (PostgreSQL; las corre el CI) |
| [pytest-matriz-ta-qa-006.txt](pytest-matriz-ta-qa-006.txt) | Matriz de seguridad de TA-QA-006 (61 rutas protegidas en `main`) | 308 passed |

Las +70 pruebas pasadas y +4 omitidas son las nuevas de EN-SEC-001. Las omitidas son las de
`tests/integration/test_en_sec_001_login_attempts_postgres.py`, que necesitan binarios de
PostgreSQL (en Windows no hay); el CI las ejecuta con `NEWGLASS_REQUIRE_POSTGRES=1`.

**Prueba de regresión del almacén:** `test_update_rereads_the_row_instead_of_the_session_copy`
falla sin `populate_existing` (`assert 2 == 3`: se pierde un fallo concurrente) y pasa con él.

## 3. Migración en PostgreSQL

| Archivo | Contenido |
|---|---|
| [alembic-offline.txt](alembic-offline.txt) | `alembic heads` (una sola cabeza, `s2sec01a1b2c`), SQL de upgrade y downgrade en modo offline y DDL del modelo ORM: son idénticos. |
| [postgres-local.txt](postgres-local.txt) | Sobre PostgreSQL 16 local: `upgrade head`, `compare_metadata` contra el ORM sin diferencias, `downgrade` a `s2cmp02d4e6f8` (la tabla desaparece), nuevo `upgrade head`, y 8 sesiones concurrentes × 10 fallos = **80 registrados, sin pérdidas**. |

## 4. Auditoría de dependencias (V-20)

| Archivo | Resultado |
|---|---|
| [pip-audit.txt](pip-audit.txt) | `main` y esta rama: **12 avisos (6 distintos)** en `python-multipart==0.0.20` → el paso nuevo falla, como debe. Rama `seguridad-owasp-zap`: 0. |
| [npm-audit.txt](npm-audit.txt) | Paso del CI (`--omit=dev --audit-level=high`): 0 en `main` y en `seguridad-owasp-zap`. Auditoría completa: `main` 10 (3 moderadas, 7 altas), `seguridad-owasp-zap` 6 altas (`picomatch`), todas de desarrollo. |

Las vulnerabilidades de `main` **no se corrigen en esta rama**: las resuelve la rama
`seguridad-owasp-zap` de Luis (python-multipart 0.0.31). Por eso el CI del backend de
EN-SEC-001 quedará en rojo en el paso de auditoría hasta que esa rama se integre y esta se
actualice con `main`.

## 5. Frontend

[frontend-suite.txt](frontend-suite.txt): lint, build y 217 pruebas unitarias OK. En la primera
corrida, en paralelo con pytest, una prueba de componente se cortó por *timeout* de 5 s; la
misma prueba falla igual en `main` bajo carga y pasa al correr la suite sola (648/648).

## 6. Escaneo OWASP ZAP (bloque D)

**Cómo se hizo:** rama temporal local `tmp/zap-scan-local` (sin push) = esta rama + la rama
`seguridad-owasp-zap` de Luis, en backend y frontend. Backend con Uvicorn en `127.0.0.1:8000`
contra el PostgreSQL efímero, con roles, permisos y dos usuarios sintéticos (Administrador y
Operario) creados para la prueba. Frontend compilado con `VITE_API_URL=http://127.0.0.1:8000`
y servido con `vite preview` en `127.0.0.1:4173`. ZAP corrió en Docker y llegó a los servicios
por `host.docker.internal`.

| Escaneo | Comando ZAP | Reporte |
|---|---|---|
| Baseline pasivo del frontend | `zap-baseline.py -t http://host.docker.internal:4173` | [zap/zap-baseline-frontend.html](zap/zap-baseline-frontend.html) · [json](zap/zap-baseline-frontend.json) |
| Baseline pasivo de la API (76 URLs de OpenAPI) | `zap-api-scan.py -f openapi -S` (modo seguro, sin ataque) | [zap/zap-baseline-api.html](zap/zap-baseline-api.html) · [json](zap/zap-baseline-api.json) |
| API autenticada, escaneo activo (195 URLs) | `zap-api-scan.py -f openapi` con `ZAP_AUTH_HEADER_VALUE` (token del administrador sintético) | [zap/zap-auth-api.html](zap/zap-auth-api.html) · [json](zap/zap-auth-api.json) |

Resumen completo: [zap/resumen-alertas.txt](zap/resumen-alertas.txt).

| Escaneo | High | Medium | Low | Informativas |
|---|---|---|---|---|
| Frontend (baseline) | 0 | 2 | 5 | 2 |
| API (baseline) | 0 | 0 | 1 | 4 |
| API autenticada (activo) | 0 | 0 | 2 | 3 |

**Comprobación de que el escaneo autenticado lo fue:** en el log del backend, durante ese
escaneo hubo 436 respuestas 200, 329 respuestas 201 y ninguna 401. Un primer intento había
dejado el token fuera (el espacio de `Bearer ...` partía el argumento `-z`) y dio 12 547
respuestas 401; se descartó y se repitió con la variable `ZAP_AUTH_HEADER_VALUE`.

### Hallazgos

| ID | Riesgo ZAP | Hallazgo | Tratamiento |
|---|---|---|---|
| Z-01 | Low | `GET /api/users/4734129394752218872` responde **500**. `app/modules/users/presentation/router.py:86` y `:96` declaran `id_usuario` sin límite y PostgreSQL rechaza un entero fuera de `INTEGER`. Otras rutas ya usan `Path(gt=0, le=2147483647)` (por ejemplo `roles_router.py:48`). El cuerpo de la respuesta no expone detalles. | Defecto previo del módulo `users` (de otro compañero). **No se corrige aquí**; se propone como tarea aparte. |
| Z-02 | Medium | Frontend sin `Content-Security-Policy` ni protección anti-clickjacking (y Low: `X-Content-Type-Options`, `Permissions-Policy`, COOP/COEP/CORP). | Lo sirve `vite preview` en local. GitHub Pages no permite cabeceras propias; la opción sería un CSP en `<meta>` en `index.html`. Fuera de alcance; se anota para el equipo. |
| Z-03 | Low | API sin `Cross-Origin-Resource-Policy`. | Mejora opcional para el middleware de Luis (N-07 de la revisión). |

Con los cambios de Luis, la API ya devuelve `X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy` y CSP: ZAP no las reporta como faltantes en ningún escaneo de la API.

### Comparación con el informe de Luis

| Aspecto | Informe de Luis (14/11/2026) | Este escaneo (18/11/2026) |
|---|---|---|
| Versión de ZAP | 2.17.0 | 2.17.0 |
| Objetivo | Solo la API, desde la raíz (sin importar OpenAPI) | Frontend y API; la API desde su OpenAPI (76 / 195 URLs) |
| Autenticación | Tokens firmados con la clave del servidor e IDs reales (B-01/B-02 de la revisión) | Login real de usuarios sintéticos |
| Escaneo activo | Máximo 30 s | Hasta completar (límite de 15 min, 2 min por regla) |
| Resultado API | 0 High, 0 Medium, 0 Low, 3 informativas | 0 High, 0 Medium, 2 Low (Z-01, Z-03), 3 informativas |
| Frontend | No escaneado | 0 High, 2 Medium, 5 Low (Z-02) |

Ambos coinciden en que no hay alertas altas ni medias en la API. Este escaneo, más amplio,
encuentra además el 500 de Z-01, que el escaneo corto sin OpenAPI no alcanzó.

## 7. Cómo reproducir

Backend (desde la raíz de `newglass-backend`, con el venv activo):

```powershell
python -m pytest tests -q --ignore=tests/unit/optimization/test_comparison_runner.py --ignore=tests/unit/optimization/test_sequential_runner.py --ignore=tests/unit/optimization/test_resource_profile.py
python -m pip install pip-audit==2.10.1
python -m pip_audit --requirement requirements.txt --progress-spinner off
```

Frontend (desde la raíz de `newglass-frontend`):

```powershell
npm ci
npm run lint
npm run build
npm run test:unit
npm run test:component
npm audit --omit=dev --audit-level=high
```

El escaneo ZAP se reproduce con los comandos de la tabla de §6, siempre contra una base local
desechable con usuarios sintéticos: el escaneo activo envía cargas de ataque y escribe datos.
