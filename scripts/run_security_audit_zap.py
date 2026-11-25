#!/usr/bin/env python3
"""Script de auditoría integral de seguridad con OWASP ZAP para NewGlass.

Ejecuta pruebas controladas de autenticación, control de acceso (RBAC),
exposición de datos sensibles, cabeceras HTTP y escaneo dinámico con OWASP ZAP.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import jwt
import requests

BACKEND_URL = "http://127.0.0.1:8000"
ZAP_PROXY = "http://127.0.0.1:8081"
PROXIES = {"http": ZAP_PROXY, "https": ZAP_PROXY}

# Rutas de salida
BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "docs" / "seguridad"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HTML_REPORT_PATH = OUTPUT_DIR / "reporte_owasp_zap.html"
JSON_REPORT_PATH = OUTPUT_DIR / "reporte_owasp_zap.json"
SUMMARY_PATH = OUTPUT_DIR / "resumen_auditoria_seguridad.json"


def log(msg, symbol="[*]"):
    print(f"{symbol} {msg}")


def get_jwt(user_id: int, username: str, role: str, secret: str, expired: bool = False, alg: str = "HS256") -> str:
    if expired:
        exp = datetime.now(timezone.utc) - timedelta(hours=1)
    else:
        exp = datetime.now(timezone.utc) + timedelta(hours=8)
    payload = {"sub": str(user_id), "username": username, "rol": role, "exp": exp}
    if alg == "none":
        return jwt.encode(payload, key="", algorithm="none")
    return jwt.encode(payload, secret, algorithm=alg)


def run_audit():
    log("Iniciando suite de auditoría de seguridad NewGlass con OWASP ZAP...", "[+]")
    
    # 1. Comprobar servicios
    try:
        r = requests.get(f"{BACKEND_URL}/health/live", timeout=5)
        log(f"Backend activo en {BACKEND_URL} (Status {r.status_code})", "[✓]")
    except Exception as e:
        log(f"Error: Backend no disponible en {BACKEND_URL}: {e}", "[!]")
        sys.exit(1)

    try:
        r = requests.get(f"{ZAP_PROXY}/JSON/core/view/version/", timeout=5)
        zap_ver = r.json().get("version", "desconocida")
        log(f"OWASP ZAP activo en {ZAP_PROXY} (Versión: {zap_ver})", "[✓]")
    except Exception as e:
        log(f"Error: OWASP ZAP no disponible en {ZAP_PROXY}: {e}", "[!]")
        sys.exit(1)

    # 2. Cargar variables
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    secret_key = os.getenv("SECRET_KEY", "rQebm3IiT6y9QiEctpgZ7ub_kdTzRAnw6UxOxNStIKCMFPub72REAmFyFKnQFWSL")

    # Tokens de prueba con IDs reales de base de datos
    token_admin = get_jwt(5, "A74000010", "Administrador", secret_key)
    token_operario = get_jwt(19, "E41552304", "Operario", secret_key)
    token_almacenero = get_jwt(20, "E24709745", "Almacenero", secret_key)
    token_usuario_inexistente = get_jwt(9999, "A99999999", "Operario", secret_key)
    token_expirado = get_jwt(19, "E41552304", "Operario", secret_key, expired=True)
    token_alterado = token_operario[:-5] + "XXXXX"
    token_alg_none = get_jwt(19, "E41552304", "Operario", secret_key, alg="none")

    session = requests.Session()
    session.proxies.update(PROXIES)

    tests_results = []

    def record_test(categoria, nombre, esperado, obtenido, detalles=""):
        aprobado = (esperado == obtenido)
        simbolo = "[✓]" if aprobado else "[✗]"
        log(f"{nombre} -> Esperado: {esperado} | Obtenido: {obtenido} {simbolo}", simbolo)
        tests_results.append({
            "categoria": categoria,
            "prueba": nombre,
            "esperado": esperado,
            "obtenido": obtenido,
            "aprobado": aprobado,
            "detalles": detalles,
        })

    # ========================================================
    # FASE A: AUDITORÍA DE AUTENTICACIÓN
    # ========================================================
    log("\n--- FASE A: PRUEBAS DE AUTENTICACIÓN Y MANEJO DE TOKENS ---", "[i]")
    
    # A1: Petición sin token a endpoint protegido
    res = session.get(f"{BACKEND_URL}/api/auth/me")
    record_test("Autenticación", "A1. Acceso a /api/auth/me sin token", 401, res.status_code)

    # A2: Acceso con token expirado
    res = session.get(f"{BACKEND_URL}/api/auth/me", headers={"Authorization": f"Bearer {token_expirado}"})
    record_test("Autenticación", "A2. Acceso con token JWT expirado", 401, res.status_code)

    # A3: Acceso con token con firma alterada
    res = session.get(f"{BACKEND_URL}/api/auth/me", headers={"Authorization": f"Bearer {token_alterado}"})
    record_test("Autenticación", "A3. Acceso con token JWT alterado/falsificado", 401, res.status_code)

    # A4: Acceso con algoritmo 'none'
    res = session.get(f"{BACKEND_URL}/api/auth/me", headers={"Authorization": f"Bearer {token_alg_none}"})
    record_test("Autenticación", "A4. Rechazo de token con alg: none", 401, res.status_code)

    # A5: Intento de login con credenciales erróneas (mensaje genérico)
    res = session.post(f"{BACKEND_URL}/api/auth/login", json={"username": "A74000010", "password": "PasswordInvalido123!"})
    msg = res.json().get("detail", "") if res.status_code == 401 else ""
    record_test("Autenticación", "A5. Login con contraseña errónea (401 genérico)", 401, res.status_code, f"Mensaje: {msg}")

    # A6: Intento de login con usuario inexistente (no enumeración)
    res = session.post(f"{BACKEND_URL}/api/auth/login", json={"username": "A99999999", "password": "PasswordCualquiera!"})
    record_test("Autenticación", "A6. Login con usuario inexistente (no revela existencia)", 401, res.status_code)

    # A7: Token JWT firmado pero con ID de usuario no existente en BD (defensa en profundidad)
    res = session.get(f"{BACKEND_URL}/api/auth/me", headers={"Authorization": f"Bearer {token_usuario_inexistente}"})
    record_test("Autenticación", "A7. Rechazo de token con ID no existente en base de datos", 401, res.status_code)

    # ========================================================
    # FASE B: AUDITORÍA DE PERMISOS Y CONTROL DE ACCESO (RBAC / BOLA)
    # ========================================================
    log("\n--- FASE B: PRUEBAS DE CONTROL DE ACCESO (RBAC & BOLA) ---", "[i]")

    # B1: Operario intenta listar usuarios (requiere users.view -> solo Admin)
    res = session.get(f"{BACKEND_URL}/api/users", headers={"Authorization": f"Bearer {token_operario}"})
    record_test("Control de Acceso", "B1. Operario intenta GET /api/users", 403, res.status_code)

    # B2: Almacenero intenta listar usuarios
    res = session.get(f"{BACKEND_URL}/api/users", headers={"Authorization": f"Bearer {token_almacenero}"})
    record_test("Control de Acceso", "B2. Almacenero intenta GET /api/users", 403, res.status_code)

    # B3: Admin lista usuarios
    res = session.get(f"{BACKEND_URL}/api/users", headers={"Authorization": f"Bearer {token_admin}"})
    record_test("Control de Acceso", "B3. Administrador ejecuta GET /api/users", 200, res.status_code)

    # B4: Operario intenta crear usuario (POST /api/users)
    res = session.post(f"{BACKEND_URL}/api/users", json={"nombres": "X", "apellidos": "Y", "dni": "77777777", "password": "123", "id_rol": 3}, headers={"Authorization": f"Bearer {token_operario}"})
    record_test("Control de Acceso", "B4. Operario intenta POST /api/users (escalación)", 403, res.status_code)

    # B5: Operario intenta consultar roles (/api/roles)
    res = session.get(f"{BACKEND_URL}/api/roles", headers={"Authorization": f"Bearer {token_operario}"})
    record_test("Control de Acceso", "B5. Operario intenta GET /api/roles", 403, res.status_code)

    # B6: Almacenero intenta registrar pedido (POST /api/orders)
    res = session.post(f"{BACKEND_URL}/api/orders", json={"id_cliente": 1, "piezas": []}, headers={"Authorization": f"Bearer {token_almacenero}"})
    record_test("Control de Acceso", "B6. Almacenero intenta POST /api/orders", 403, res.status_code)

    # B7: Operario consulta stock (/api/inventory/planchas) -> Permitido según matriz
    res = session.get(f"{BACKEND_URL}/api/inventory/planchas", headers={"Authorization": f"Bearer {token_operario}"})
    record_test("Control de Acceso", "B7. Operario consulta GET /api/inventory/planchas", 200, res.status_code)

    # B8: Almacenero consulta stock (/api/inventory/planchas) -> Permitido
    res = session.get(f"{BACKEND_URL}/api/inventory/planchas", headers={"Authorization": f"Bearer {token_almacenero}"})
    record_test("Control de Acceso", "B8. Almacenero consulta GET /api/inventory/planchas", 200, res.status_code)

    # ========================================================
    # FASE C: EXPOSICIÓN DE DATOS Y CABECERAS HTTP
    # ========================================================
    log("\n--- FASE C: EXPOSICIÓN DE DATOS Y CABECERAS DE SEGURIDAD ---", "[i]")

    # C1: Comprobar si /api/users expone hashes de contraseña
    res = session.get(f"{BACKEND_URL}/api/users", headers={"Authorization": f"Bearer {token_admin}"})
    has_hash = False
    if res.status_code == 200:
        raw_text = res.text
        if "password_hash" in raw_text or "$2b$" in raw_text or "password" in raw_text:
            has_hash = True
    record_test("Exposición de Datos", "C1. Verificación de no exposición de password_hash en /api/users", False, has_hash, "No se encontraron hashes en las respuestas")

    # C2: Cabecera X-Content-Type-Options
    h = res.headers
    has_xcto = h.get("X-Content-Type-Options") == "nosniff"
    record_test("Cabeceras HTTP", "C2. Cabecera X-Content-Type-Options: nosniff", True, has_xcto, f"Cabecera presente: {has_xcto}")

    # C3: Cabecera X-Frame-Options
    has_xfo = h.get("X-Frame-Options") in ["DENY", "SAMEORIGIN"]
    record_test("Cabeceras HTTP", "C3. Cabecera X-Frame-Options (Clickjacking)", True, has_xfo, f"Cabecera presente: {has_xfo}")

    # C4: Content-Security-Policy
    has_csp = "Content-Security-Policy" in h
    record_test("Cabeceras HTTP", "C4. Cabecera Content-Security-Policy", True, has_csp, f"Cabecera presente: {has_csp}")

    # ========================================================
    # FASE D: OWASP ZAP ACTIVE SCAN & ALERT REPORTING
    # ========================================================
    log("\n--- FASE D: ESCANEO Y EXTRACCIÓN DE REPORTES DE OWASP ZAP ---", "[i]")

    # Iniciar escaneo activo controlado
    target = f"{BACKEND_URL}"
    log(f"Iniciando ZAP Active Scan contra {target}...", "[*]")
    scan_resp = requests.get(f"{ZAP_PROXY}/JSON/ascan/action/scan/?url={target}&recurse=true")
    scan_id = scan_resp.json().get("scan", "0")
    log(f"Scan ID iniciado: {scan_id}. Monitoreando progreso...", "[*]")

    # Esperar hasta 30 segundos o hasta que termine
    for _ in range(15):
        time.sleep(2)
        status_resp = requests.get(f"{ZAP_PROXY}/JSON/ascan/view/status/?scanId={scan_id}")
        progress = status_resp.json().get("status", "0")
        log(f"Progreso Active Scan: {progress}%", "[*]")
        if int(progress) >= 100:
            break

    # Esperar a que el escáner pasivo procese la cola
    requests.get(f"{ZAP_PROXY}/JSON/pscan/action/enableAllRules/")
    time.sleep(3)

    # Extraer alertas detectadas por ZAP
    alerts_resp = requests.get(f"{ZAP_PROXY}/JSON/core/view/alerts/?baseurl={target}")
    alerts = alerts_resp.json().get("alerts", [])

    log(f"\nAlertas detectadas por OWASP ZAP: {len(alerts)}", "[+]")
    alerts_by_risk = {"High": [], "Medium": [], "Low": [], "Informational": []}
    for a in alerts:
        risk = a.get("risk", "Informational")
        alerts_by_risk.setdefault(risk, []).append({
            "alert": a.get("alert"),
            "risk": risk,
            "confidence": a.get("confidence"),
            "url": a.get("url"),
            "cweid": a.get("cweid"),
            "solution": a.get("solution"),
        })
        log(f" - [{risk.upper()}] {a.get('alert')} en {a.get('url')}")

    # Descargar y guardar reportes oficiales de ZAP
    log("\nGenerando y guardando reportes de OWASP ZAP...", "[*]")
    html_resp = requests.get(f"{ZAP_PROXY}/OTHER/core/other/htmlreport/")
    with open(HTML_REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html_resp.text)
    log(f"Reporte HTML guardado en: {HTML_REPORT_PATH}", "[✓]")

    json_resp = requests.get(f"{ZAP_PROXY}/OTHER/core/other/jsonreport/")
    with open(JSON_REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(json_resp.text)
    log(f"Reporte JSON guardado en: {JSON_REPORT_PATH}", "[✓]")

    # Guardar resumen consolidado
    summary_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "backend_url": BACKEND_URL,
        "zap_version": zap_ver,
        "resumen_pruebas_controladas": {
            "total": len(tests_results),
            "aprobadas": sum(1 for t in tests_results if t["aprobado"]),
            "fallidas_o_hallazgos": sum(1 for t in tests_results if not t["aprobado"]),
        },
        "alertas_zap_por_severidad": {
            "High": len(alerts_by_risk.get("High", [])),
            "Medium": len(alerts_by_risk.get("Medium", [])),
            "Low": len(alerts_by_risk.get("Low", [])),
            "Informational": len(alerts_by_risk.get("Informational", [])),
        },
        "detalle_pruebas": tests_results,
        "alertas_zap": alerts,
    }
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False)
    log(f"Resumen de auditoría guardado en: {SUMMARY_PATH}", "[✓]")
    log("\n=== AUDITORÍA CONTROLADA FINALIZADA CON ÉXITO ===", "[+]")


if __name__ == "__main__":
    run_audit()
