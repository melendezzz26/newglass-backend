"""Datos sintéticos para la carga: solo en el PostgreSQL temporal de loopback.

Uso (lo llama run_load.py):
    LOAD_PASSWORD=... DATABASE_URL=postgresql+psycopg://...@127.0.0.1:.../... \
        python -m experiments.load.seed_data --manifest RUTA.json

La contraseña sintética llega por el entorno, se usa para un único hash bcrypt y no
se escribe en el manifiesto ni en ningún otro archivo.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import random

from sqlalchemy import select, update

from experiments.load.common import ACCOUNT_POOLS, require_loopback_database, username

ACCOUNTS_PER_READ_ROLE = 80
COMPARISON_ACCOUNTS = 10
CONFIRM_ORDERS = 60  # E3: 3 corridas x 20 repeticiones
READ_ORDERS = 300
THICKNESS = Decimal("6.0")
DNI_OFFSET = {"admin": 10000000, "almacenero": 20000000, "operario": 30000000,
              "comparison": 40000000, "confirm": 50000000}


def rectangle(width, height, quantity, glass):
    return dict(tipo_forma="RECTANGULO", cantidad=quantity,
                dimensiones={"width_mm": width, "height_mm": height},
                geometria={"type": "RECTANGULO", "width_mm": width, "height_mm": height},
                id_tipo_vidrio=glass, espesor_mm=THICKNESS, area_mm2=width * height)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    require_loopback_database(os.environ["DATABASE_URL"])
    password = os.environ["LOAD_PASSWORD"]

    # Importar después del control de loopback: SessionLocal lee DATABASE_URL.
    from app.models import (Cliente, Configuracion, Plancha, Retazo, Rol, TipoVidrio,
                            TipoVidrioEspesor, Usuario)
    from app.modules.orders.infrastructure.repositories import SQLAlchemyOrderRepository
    from app.shared.database import SessionLocal
    from app.shared.security import hash_password
    from seed import crear_roles

    crear_roles()  # roles + permisos iniciales (mismo camino que seed.py)
    rng = random.Random(20261125)
    now = datetime.now(timezone.utc)
    password_hash = hash_password(password)
    accounts = {}
    with SessionLocal.begin() as db:
        roles = dict(db.execute(select(Rol.nombre, Rol.id_rol)).all())
        counts = {pool: ACCOUNTS_PER_READ_ROLE for pool in ("admin", "almacenero", "operario")}
        counts.update(comparison=COMPARISON_ACCOUNTS, confirm=1)
        for pool, total in counts.items():
            role = ACCOUNT_POOLS[pool][1]
            accounts[pool] = []
            for index in range(total):
                user = Usuario(dni=f"{DNI_OFFSET[pool] + index:08d}", usuario=username(pool, index),
                               nombres="Carga", apellidos=f"{pool} {index}", password_hash=password_hash,
                               estado=True, fecha_creacion=now, id_rol=roles[role])
                db.add(user)
                db.flush()
                accounts[pool].append(user.id_usuario)

        glass = {}
        for key, name in (("read", "QA carga lecturas"), ("comparison", "QA carga comparación"),
                          ("confirm", "QA carga confirmación")):
            row = TipoVidrio(nombre=name, descripcion="Material sintético de la prueba de carga", estado=True)
            db.add(row)
            db.flush()
            db.add(TipoVidrioEspesor(id_tipo_vidrio=row.id_tipo_vidrio, espesor_mm=THICKNESS))
            glass[key] = row.id_tipo_vidrio
        db.flush()

        # Configuración vigente igual a la observación de Andro: raster, margen y separación 10 mm, paso 5°.
        db.execute(update(Configuracion).values(vigente=False))
        last = db.scalar(select(Configuracion.version).order_by(Configuracion.version.desc()).limit(1)) or 0
        db.add(Configuracion(version=last + 1, separacion_mm=10, margen_mm=10, resolucion_raster_mm=10,
                             paso_angular_grados=5, ancho_min_retazo_mm=1, alto_min_retazo_mm=1, vigente=True,
                             id_usuario_creacion=accounts["admin"][0], fecha_creacion=now))

        clients = []
        for index in range(40):
            client = Cliente(tipo_documento="RUC", numero_documento=f"20{index:09d}",
                             nombre_razon_social=f"Cliente sintético {index:02d}", estado=True, fecha_registro=now)
            db.add(client)
            db.flush()
            clients.append(client.id_cliente)

        for index in range(60):
            db.add(Plancha(ancho_mm=rng.choice([1830, 2140, 2440, 3300]), alto_mm=rng.choice([1220, 1600, 2140]),
                           espesor_mm=THICKNESS, cantidad=rng.randint(1, 10), estado=True,
                           fecha_registro=now, id_tipo_vidrio=glass["read"]))
        for index in range(120):
            width, height = rng.randint(200, 900), rng.randint(200, 900)
            db.add(Retazo(codigo=f"QA-CARGA-{index:04d}", espesor_mm=THICKNESS,
                          geometria={"type": "RECTANGULO", "width_mm": width, "height_mm": height},
                          area_mm2=width * height, estado=True, fecha_registro=now, id_tipo_vidrio=glass["read"]))
        # E2: una plancha 1000 x 1000 con cantidad 2 (fixture de Andro). E3: cantidad 40 en otro material.
        db.add(Plancha(ancho_mm=1000, alto_mm=1000, espesor_mm=THICKNESS, cantidad=2, estado=True,
                       fecha_registro=now, id_tipo_vidrio=glass["comparison"]))
        confirm_sheet = Plancha(ancho_mm=1000, alto_mm=1000, espesor_mm=THICKNESS, cantidad=40, estado=True,
                                fecha_registro=now, id_tipo_vidrio=glass["confirm"])
        db.add(confirm_sheet)
        db.flush()
        confirm_sheet_id = confirm_sheet.id_plancha

    orders = {"read": [], "comparison": [], "confirm": []}
    with SessionLocal() as db:
        repo = SQLAlchemyOrderRepository(db)
        writers = accounts["operario"] + accounts["admin"]
        for index in range(READ_ORDERS):
            pieces = [rectangle(rng.randint(200, 1200), rng.randint(200, 1200), rng.randint(1, 6), glass["read"])
                      for _ in range(2)]
            orders["read"].append(repo.create_order(rng.choice(writers), pieces, id_cliente=rng.choice(clients)))
        for actor in accounts["comparison"]:
            orders["comparison"].append(repo.create_order(actor, [rectangle(100, 100, 50, glass["comparison"])],
                                                          id_cliente=clients[0]))
        for _ in range(CONFIRM_ORDERS):
            orders["confirm"].append(repo.create_order(accounts["confirm"][0],
                                                       [rectangle(100, 100, 10, glass["confirm"])],
                                                       id_cliente=clients[1]))

    manifest = {
        "accounts": {pool: [{"username": username(pool, i), "user_id": uid} for i, uid in enumerate(ids)]
                     for pool, ids in accounts.items()},
        "orders": orders,
        "glass": glass,
        "confirm_sheet_id": confirm_sheet_id,
        "counts": {"accounts": {pool: len(ids) for pool, ids in accounts.items()},
                   "read_orders": READ_ORDERS, "clients": 40, "read_sheets": 60, "read_remnants": 120,
                   "comparison_orders": COMPARISON_ACCOUNTS, "confirm_orders": CONFIRM_ORDERS},
    }
    with open(args.manifest, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    print(f"seed_ok accounts={sum(manifest['counts']['accounts'].values())} orders={sum(map(len, orders.values()))}")


if __name__ == "__main__":
    main()
