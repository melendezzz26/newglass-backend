# Informe Formal de Auditoría y Remediación de Seguridad
## Proyecto NewGlass — Frontend y Backend

**Responsable:** Luis I. (Seguridad)  
**Fecha:** 14 de Noviembre de 2026  
**Rama:** `seguridad-owasp-zap`  
**Herramientas Utilizadas:**
* **OWASP ZAP 2.17.0** (DAST — Dynamic Application Security Testing)
* **Bandit 1.9.4** (SAST — Static Application Security Testing para Python)
* **pip-audit 2.10.1** (SCA — Software Composition Analysis para Python)
* **npm audit** (SCA — Software Composition Analysis para Node/React)
* **FastAPI Security Test Harness** (Auditoría controlada de RBAC y Autenticación)

---

## 1. Resumen Ejecutivo y Métricas

Se ejecutó una auditoría de seguridad integral sobre los repositorios desacoplados `newglass-backend` (FastAPI) y `newglass-frontend` (React 19 + Vite), evaluando:
1. **Autenticación y Sesión:** JWT (HS256), hashing con Bcrypt, gestión de expiración y firma.
2. **Control de Acceso (RBAC / BOLA):** Cumplimiento estricto de la matriz de permisos por rol (Administrador, Almacenero, Operario).
3. **Exposición de Datos:** Prevención de fugas de credenciales, contraseñas y metadatos.
4. **Hardening de Cabeceras HTTP:** Mitigación de Clickjacking y MIME-sniffing.
5. **Vulnerabilidades en Dependencias:** Eliminación de CVEs en librerías de terceros.

### Métricas de Remediación (Antes vs. Después)

| Indicador de Seguridad | Diagnóstico Inicial | Post-Remediación | Estado Final |
| :--- | :---: | :---: | :---: |
| **Vulnerabilidades en Backend (pip-audit)** | 12 conocidas | **0** | **100% Mitigado** |
| **Vulnerabilidades en Frontend (npm audit)** | 6 (3 High, 3 Mod) | **0** | **100% Mitigado** |
| **Hallazgos SAST Bandit (14,793 líneas)** | 1 (Low CWE-703) | **0** | **100% Mitigado** |
| **Alertas de Riesgo en OWASP ZAP (DAST)** | 5 alertas Low (cabeceras) | **0 Low, 0 Med, 0 High** | **100% Mitigado** |
| **Pruebas de Autenticación (A1 - A7)** | 7 evaluadas | 7 pasadas | **100% Conforme** |
| **Pruebas de Permisos RBAC (B1 - B8)** | 8 evaluadas | 8 pasadas | **100% Conforme** |
| **Suites de Pruebas Unitarias Backend** | 456 pasadas | 456 pasadas | **Sin Regresiones** |
| **Suites de Pruebas Unitarias Frontend** | 217 pasadas | 217 pasadas | **Sin Regresiones** |

---

## 2. Matriz de Hallazgos y Remediaciones

### Hallazgo SEC-01: Ausencia de Cabeceras HTTP de Seguridad
* **Severidad:** Baja (Detectado por OWASP ZAP en múltiples endpoints).
* **CWE:** CWE-693 (Protection Mechanism Failure), CWE-1021 (Improper Restriction of Rendered UI Layers).
* **Impacto:** Exposición a ataques de Clickjacking y MIME-type sniffing en navegadores legacy.
* **Remediación Aplicada:**
  Se implementó `SecurityHeadersMiddleware` en `app/shared/security/headers.py` y se registró en `main.py`:
  * `X-Content-Type-Options: nosniff`
  * `X-Frame-Options: DENY`
  * `Referrer-Policy: strict-origin-when-cross-origin`
  * `X-Permitted-Cross-Domain-Policies: none`
  * `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'; object-src 'none';`
* **Verificación:** ZAP eliminó el 100% de las alertas de cabeceras faltantes.

### Hallazgo SEC-02: Dependencia Vulnerable `python-multipart`
* **Severidad:** Alta / Moderada (PYSEC-2026-1852, PYSEC-2026-3036 a 3040).
* **Componente Afectado:** `python-multipart==0.0.20` utilizado en `/api/orders/import/preview`.
* **Impacto:** Vulnerabilidad a DoS por deserialización o consumo anómalo de memoria en subida de archivos multipart.
* **Remediación Aplicada:**
  Se actualizó `requirements.txt` a `python-multipart==0.0.31`. `pip-audit` confirmó 0 vulnerabilidades conocidas.

### Hallazgo SEC-03: Vulnerabilidades en Dependencias de Frontend
* **Severidad:** Alta / Moderada (GHSA-g3hv-75wg-m249, GHSA-4w7w-5xrc-9h26, GHSA-698v-4r3x-3vcv, GHSA-mrhx-c7xc-m3w5).
* **Componente Afectado:** `undici`, `vite`, `zod`, `minimatch`.
* **Remediación Aplicada:**
  Ejecución de `npm audit fix` en `newglass-frontend`, actualizando los árboles de dependencias transitivas seguras en `package-lock.json`. `npm audit` reportó 0 vulnerabilidades.

### Hallazgo SEC-04: Captura Silenciosa de Excepciones en Healthcheck
* **Severidad:** Baja (Bandit B110 / CWE-703).
* **Componente Afectado:** `app/shared/observability/health.py:63`.
* **Remediación Aplicada:**
  Se sustituyó el bloque `except Exception: pass` por logging defensivo tipado (`TimeoutError`, `asyncio.TimeoutError` y `Exception`) con `logger.warning` / `logger.debug`.

---

## 3. Resultados Detallados de Pruebas Controladas

### A. Autenticación y Manejo de Tokens
| ID | Caso de Prueba | Entrada / Condición | Respuesta Esperada | Respuesta Obtenida | Estado |
| :--- | :--- | :--- | :---: | :---: | :---: |
| **CP-AUTH-01** | Petición sin token | `GET /api/auth/me` sin cabecera Authorization | HTTP 401 | HTTP 401 | **Aprobado** |
| **CP-AUTH-02** | Token caducado | JWT con claim `exp` en el pasado | HTTP 401 | HTTP 401 | **Aprobado** |
| **CP-AUTH-03** | Firma alterada | JWT con bytes de firma modificados | HTTP 401 | HTTP 401 | **Aprobado** |
| **CP-AUTH-04** | Algoritmo nulo | JWT con cabecera `alg: "none"` | HTTP 401 | HTTP 401 | **Aprobado** |
| **CP-AUTH-05** | Password erróneo | `POST /api/auth/login` con password inválido | HTTP 401 (genérico) | HTTP 401 | **Aprobado** |
| **CP-AUTH-06** | Usuario inexistente | `POST /api/auth/login` con DNI no registrado | HTTP 401 (genérico) | HTTP 401 | **Aprobado** |
| **CP-AUTH-07** | ID huérfano | JWT válido con `sub` inexistente en BD | HTTP 401 | HTTP 401 | **Aprobado** |

### B. Control de Acceso y Prevención de Escalación (RBAC / BOLA)
| ID | Caso de Prueba | Rol Emisor | Endpoint Objetivo | Respuesta | Estado |
| :--- | :--- | :--- | :--- | :---: | :---: |
| **CP-RBAC-01** | Consulta no autorizada de usuarios | Operario | `GET /api/users` | HTTP 403 | **Aprobado** |
| **CP-RBAC-02** | Consulta no autorizada de usuarios | Almacenero | `GET /api/users` | HTTP 403 | **Aprobado** |
| **CP-RBAC-03** | Consulta legítima de usuarios | Administrador | `GET /api/users` | HTTP 200 | **Aprobado** |
| **CP-RBAC-04** | Creación no autorizada de usuario | Operario | `POST /api/users` | HTTP 403 | **Aprobado** |
| **CP-RBAC-05** | Consulta no autorizada de roles | Operario | `GET /api/roles` | HTTP 403 | **Aprobado** |
| **CP-RBAC-06** | Creación no autorizada de pedidos | Almacenero | `POST /api/orders` | HTTP 403 | **Aprobado** |
| **CP-RBAC-07** | Consulta permitida de stock | Operario | `GET /api/inventory/planchas` | HTTP 200 | **Aprobado** |
| **CP-RBAC-08** | Consulta permitida de stock | Almacenero | `GET /api/inventory/planchas` | HTTP 200 | **Aprobado** |

---

## 4. Evidencias Generadas por OWASP ZAP (DAST)

Los archivos oficiales generados en la auditoría dinámica se encuentran disponibles en:
1. **Reporte Completo en HTML:** [`docs/seguridad/reporte_owasp_zap.html`](reporte_owasp_zap.html)
2. **Reporte Estructurado en JSON:** [`docs/seguridad/reporte_owasp_zap.json`](reporte_owasp_zap.json)
3. **Resumen de Ejecución Automatizada:** [`docs/seguridad/resumen_auditoria_seguridad.json`](resumen_auditoria_seguridad.json)
4. **Arnés de Pruebas Automatizadas:** [`scripts/run_security_audit_zap.py`](../../scripts/run_security_audit_zap.py)

---
*Fin del informe de auditoría y remediación de seguridad.*
