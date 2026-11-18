# Revisión de la rama `seguridad-owasp-zap` (backend y frontend)

| Campo | Valor |
|---|---|
| Autor de la rama | Luis Anthony Ibañez Herrera |
| Revisor | Fabricio Aguilar |
| Backend revisado | `newglass-backend` `seguridad-owasp-zap` en `3a7a335` (base `main` `2afa9d7`) |
| Frontend revisado | `newglass-frontend` `seguridad-owasp-zap` en `e715162` (base `main` `428652b`) |
| Fecha | 18/11/2026 |
| Forma de revisión | Checkout en worktree aparte, sin modificar la rama. Suites del CI en Windows y sondas locales sin base remota. |

## Resumen

El cambio de código es correcto y útil: el middleware de cabeceras es ASGI puro y no rompe
el streaming, y python-multipart 0.0.31 elimina los 12 avisos de `pip-audit` sin cambiar el
comportamiento de la importación masiva. Hay **3 observaciones bloqueantes**: dos en el script
de auditoría (una clave escrita en el código y usuarios reales con su DNI) y una regresión
funcional (el CSP rompe `/docs` y `/redoc`). El resto son ajustes del informe y mejoras.

## Resultados de las suites

| Repo | Comando | Resultado |
|---|---|---|
| backend | `python -m pytest tests -q` con los 3 `--ignore` de Windows | 2748 passed, 955 skipped, 0 failed (igual que `main`: 2748 / 955) |
| frontend | `npm ci` | OK; avisa 6 vulnerabilidades *high* (ver N-02) |
| frontend | `npm run lint` | OK |
| frontend | `npm run build` | OK |
| frontend | `npm run test:unit` | 217 pass, 0 fail |
| frontend | `npm run test:component` | 648 passed (63 archivos) al correrla sola. En la primera corrida, en paralelo con pytest, `ComparisonWorkspaceV2.test.jsx > manual v2 confirmation keeps history after reload, lost response=false` se cortó por *timeout* de 5 s; lo mismo ocurre en `main` sin los cambios de la rama, así que no es una regresión de esta rama. |

## Observaciones bloqueantes

### B-01 — Clave de firma JWT escrita en el código

- **Archivo:** `scripts/run_security_audit_zap.py:67`
- **Qué pasa:** `os.getenv("SECRET_KEY", "...")` trae como respaldo una clave de 64 caracteres
  escrita en el código. Con ella cualquiera puede firmar tokens válidos para cualquier backend
  que use esa misma clave. La clave ya está en el historial remoto de la rama.
- **Qué pedir:**
  1. Quitar el valor de respaldo. Si `SECRET_KEY` no está definida, el script debe detenerse
     con un mensaje claro (como `required_value` en `app/shared/config`).
  2. Si esa clave se usó alguna vez en un `.env` local compartido o en Staging, **rotarla**:
     ya no se puede tratar como secreta, aunque después se reescriba el historial.
  3. Mejor todavía: obtener los tokens con `POST /api/auth/login` de usuarios sintéticos, en
     vez de firmarlos con la clave del servidor.

### B-02 — Usuarios reales (con DNI) en el script y en el reporte

- **Archivos:** `scripts/run_security_audit_zap.py:69-76` y
  `docs/seguridad/resumen_auditoria_seguridad.json:200`
- **Qué pasa:** el script usa "IDs reales de base de datos" con tres usuarios de acceso. En
  NewGlass el usuario es la inicial del nombre más el DNI
  (`generate_access_username(first_names, dni)` en
  `app/modules/users/application/use_cases.py:47`), así que esos valores **exponen DNIs**. Uno
  de ellos queda también en el resumen JSON (`userValue=...`).
- **Qué pedir:** usar solo cuentas sintéticas creadas para la prueba (por ejemplo, con la
  forma de los fixtures de `tests/conftest.py`) y regenerar el resumen sin esos valores.

### B-03 — El CSP rompe `/docs` y `/redoc`

- **Archivos:** `app/shared/security/headers.py:19` (registrado en `main.py:50`)
- **Qué pasa:** `default-src 'self'` se aplica a todas las respuestas. La página de Swagger UI
  carga su JS y CSS desde `cdn.jsdelivr.net` y tiene un `<script>` inline; ReDoc carga su JS
  desde `cdn.jsdelivr.net` y fuentes desde `fonts.googleapis.com`. El navegador bloquea todo
  eso y ambas páginas quedan en blanco. Comprobado con una sonda local:

  ```
  /docs:  csp="default-src 'self'; frame-ancestors 'none'; object-src 'none';"
    recursos externos: cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js,
                       cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css,
                       fastapi.tiangolo.com/img/favicon.png
    bloques <script> inline: 1
  /redoc: csp="default-src 'self'; frame-ancestors 'none'; object-src 'none';"
    recursos externos: cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js,
                       fastapi.tiangolo.com/img/favicon.png,
                       fonts.googleapis.com/css?family=Montserrat...|Roboto...
  ```

- **Corrección propuesta** (mantiene el CSP estricto para toda la API y relaja solo las
  páginas de documentación):

  ```python
  API_CSP = "default-src 'self'; frame-ancestors 'none'; object-src 'none';"
  # Swagger UI y ReDoc de FastAPI cargan recursos de CDN y un script inline.
  DOCS_CSP = (
      "default-src 'self'; "
      "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
      "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
      "font-src 'self' https://fonts.gstatic.com; "
      "img-src 'self' data: https://fastapi.tiangolo.com; "
      "worker-src 'self' blob:; "
      "frame-ancestors 'none'; object-src 'none';"
  )
  DOCS_PATHS = ("/docs", "/redoc")


  def _csp_for(path: str) -> str:
      is_docs = path in DOCS_PATHS or path.startswith("/docs/")  # incluye /docs/oauth2-redirect
      return DOCS_CSP if is_docs else API_CSP
  ```

  En `send_wrapper`, usar `headers.setdefault("content-security-policy", _csp_for(scope["path"]))`
  y quitar el CSP de `SECURITY_HEADERS`. Agregar una prueba que verifique que `/docs` permite
  `cdn.jsdelivr.net` y que una ruta `/api/...` conserva `default-src 'self'`. Validar en un
  navegador que Swagger y ReDoc cargan antes del merge.
- **Alternativa** (requiere decisión del equipo): desactivar `/docs` y `/redoc` en Staging con
  `docs_url=None, redoc_url=None`. Hoy el equipo las usa en la demo, por eso propongo la
  opción anterior.

## Observaciones no bloqueantes

### N-01 — Las cifras del informe no son las actuales

- `docs/seguridad/INFORME_SEGURIDAD_OWASP_ZAP.md:35-36` dice "456 pasadas" (backend) y
  "217 pasadas" (frontend). Hoy son 2748 passed / 955 skipped en backend (Windows) y, en
  frontend, 217 unitarias más 648 de componentes. Conviene poner el comando, la fecha y el
  commit de cada cifra.

### N-02 — `npm audit` ya no da 0 en el frontend

- `docs/seguridad/INFORME_SEGURIDAD_OWASP_ZAP.md:30` y `:66` dicen que `npm audit` reporta 0.
  Con el `package-lock.json` de la rama, `npm audit` reporta hoy **6 high** por `picomatch`
  (GHSA-fc5q-39xc-vjpf, ReDoS), que llega por `vite`, `vitest`, `tinyglobby`, `fdir` y
  `@vitejs/plugin-react`. **Todas son dependencias de desarrollo**:
  `npm audit --omit=dev --audit-level=high` da 0. Es un aviso nuevo; corregirlo exige
  `npm audit fix --force` (cambio mayor de vitest), así que no conviene hacerlo en esta rama.
  Basta con dejarlo escrito así en el informe.

### N-03 — Diff de `package-lock.json`: sin saltos mayores en dependencias directas

- `package.json` no cambia. Cambian 34 paquetes, todos de desarrollo y transitivos.
- `vite` 8.3.1 → 8.3.8 (parche). `eslint` 10.11.0 → 10.15.0, `postcss` 8.5.28 → 8.5.33,
  `undici` 8.11.2 → 8.12.4, `zod` 4.6.5 → 4.6.7, `rolldown` 1.2.11 → 1.2.19 (menores o parches).
- Saltos mayores, solo transitivos y de desarrollo: `minimatch` 10.2.6 → 11.0.8 y
  `@oxc-project/types` 0.151.0 → 0.156.0.
- Entran dependencias opcionales nuevas para el binding WASM de rolldown, entre ellas
  `@emnapi/core` y `@emnapi/runtime` **2.0.0-beta.2** (prerelease). Lint, build y pruebas pasan.
  Se puede aceptar; solo dejarlo anotado.

### N-04 — El escaneo activo dura como máximo 30 s

- `scripts/run_security_audit_zap.py:207-213` espera 15 × 2 s y sigue aunque el progreso no
  llegue a 100 %. El resultado "0 alertas" puede venir de un escaneo incompleto. Sugerencia:
  esperar hasta 100 % con un tiempo máximo configurable y guardar el progreso final en el
  resumen.

### N-05 — El escaneo activo escribe en la base que use el backend

- `scripts/run_security_audit_zap.py:65-67` carga `.env` y el escaneo activo (`:202`) manda
  cargas de ataque (POST, PATCH) a `127.0.0.1:8000`. Si ese backend local apunta a la base de
  Staging, el escaneo deja datos basura ahí. Sugerencia: documentar en el informe y en el
  script que solo se ejecuta contra una base local desechable, nunca contra Staging.

### N-06 — `requests` no está en `requirements.txt`

- `scripts/run_security_audit_zap.py:14` importa `requests`, que no está en
  `requirements.txt`. En un entorno limpio el script falla. Se puede usar `httpx`, que ya está
  en el archivo.

### N-07 — Endurecimiento opcional de cabeceras

- `app/shared/security/headers.py:9-20`: valorar `Strict-Transport-Security` (Heroku sirve por
  HTTPS) y `Cache-Control: no-store` en `/api/auth/*`, porque la respuesta de login lleva el
  token. Las respuestas 500 que genera `ServerErrorMiddleware` de Starlette no pasan por este
  middleware; es menor.

### N-08 — Detalles menores

- `app/shared/observability/health.py:65`: en Python 3.11 `asyncio.TimeoutError` es el mismo
  `TimeoutError`; la tupla es redundante pero no hace daño. El cambio de `pass` a logging sin
  mensaje de excepción está bien.
- `scripts/run_security_audit_zap.py:21-23` crea `docs/seguridad` al importar el módulo.
  Mejor hacerlo dentro de `run_audit()`.
- La convención del equipo para evidencia es `docs/evidence/sprint-02/<ID>/`; `docs/seguridad/`
  funciona, pero conviene enlazarla desde ahí.

## Verificaciones que pasan (para dejar constancia)

1. **`SecurityHeadersMiddleware` es ASGI puro y no rompe el streaming.** Solo modifica el
   mensaje `http.response.start` y deja pasar el cuerpo sin almacenarlo. Sonda con un
   `StreamingResponse` NDJSON como el de comparisons-v2: los fragmentos llegan a 0.02 s,
   0.33 s y 0.63 s (uno por evento, sin esperar al final), con las cabeceras presentes. Ante
   una desconexión, el generador se cierra (su `finally` se ejecuta). Está registrado después
   de CORS, así que es el más externo y también cubre las respuestas de preflight.
2. **python-multipart 0.0.31 con `/api/orders/import/preview`.** Las 20 pruebas de API de
   importación necesitan PostgreSQL y en Windows se omiten (el CI las corre). Sonda sin base,
   misma ruta y mismas entradas con 0.0.20 (`main`) y 0.0.31 (rama), resultados idénticos:

   | Caso | 0.0.20 | 0.0.31 |
   |---|---|---|
   | CSV de 5 filas | 200, 3 válidas / 2 observadas | igual |
   | XLSX de 2 filas | 200, 2 válidas | igual |
   | Nombre de archivo con tildes y ñ | 200 | igual |
   | Archivo de 1 MB | 422 (límite de 2000 filas) | igual |
   | Archivo > 1 MB | 413 | igual |
   | Sin archivo | 422 | igual |

   `pip-audit 2.10.1`: `main` tiene 12 avisos (6 distintos) en python-multipart 0.0.20; la rama
   no tiene ninguno.
3. **El informe no expone datos de conexión ni hosts.** En `docs/seguridad/` solo aparece
   `127.0.0.1` (loopback); no hay URLs de base, hosts de Supabase o Heroku, tokens ni rutas
   locales. La única exposición es el usuario de B-02.

## Orden de integración sugerido

1. EN-SEC-001 (backend) ya incluye la actualización de `python-multipart` a 0.0.31 (commit
   `fix(deps): update python-multipart to 0.0.31`), porque su paso nuevo de `pip-audit` fallaba
   en `main` por la versión 0.0.20.
2. Al rehacer esta rama sobre `main` actualizado, **quitar el cambio de `requirements.txt`**
   para no duplicarlo ni generar conflictos; el resto de la rama no cambia.
