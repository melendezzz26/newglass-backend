"""Regresión: un id_usuario fuera del rango INTEGER nunca produce un error 500.

Hallazgo del escaneo OWASP ZAP de EN-SEC-001: GET /api/users/4734129394752218872
respondía 500 en PostgreSQL. Ahora se rechaza con 422 antes de consultar la base,
igual que roles_router.py (Path(gt=0, le=2147483647)).
"""
import pytest

from app.shared.security import create_access_token

ADMIN = "F70000201"
MAX_INT = 2147483647
OUT_OF_RANGE = [
    MAX_INT + 1,
    4734129394752218872,  # valor que usó ZAP
    99999999999999999999,  # mayor que 2^63: también fallaba con SQLite
    0,
    -1,
]


@pytest.fixture
def admin(create_user) -> dict:
    user_id = create_user(ADMIN, "Administrador")
    return {"Authorization": "Bearer " + create_access_token(user_id, ADMIN, "Administrador")}


def assert_rejected_without_details(response):
    assert response.status_code == 422, response.text
    body = response.text.lower()
    for leaked in ("sqlalchemy", "psycopg", "select", "traceback", "integer out of range"):
        assert leaked not in body


@pytest.mark.parametrize("user_id", OUT_OF_RANGE)
def test_get_out_of_range_id_is_422_not_500(client, admin, user_id):
    assert_rejected_without_details(client.get(f"/api/users/{user_id}", headers=admin))


@pytest.mark.parametrize("user_id", OUT_OF_RANGE)
def test_patch_out_of_range_id_is_422_not_500(client, admin, user_id):
    response = client.patch(f"/api/users/{user_id}", json={"nombres": "Nadie"}, headers=admin)
    assert_rejected_without_details(response)


@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_largest_valid_id_still_reaches_the_use_case_and_is_404(client, admin, method):
    response = client.request(method, f"/api/users/{MAX_INT}", headers=admin,
                              json={"nombres": "Nadie"} if method == "PATCH" else None)
    assert response.status_code == 404
    assert response.json() == {"detail": "Usuario no encontrado."}


def test_authentication_is_still_checked_before_the_id(client):
    assert client.get(f"/api/users/{MAX_INT + 1}").status_code == 401
