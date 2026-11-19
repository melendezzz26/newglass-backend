"""Read-only fingerprints of optimization history, independent of application startup."""
import sqlalchemy as sa


def revision(engine):
    with engine.connect() as connection:
        return connection.scalar(sa.text("SELECT version_num FROM alembic_version"))


def history_bytes(engine, *, include_ff_request_key=True):
    tables = {"optimizaciones": "id_optimizacion", "ejecuciones_optimizacion": "id_ejecucion",
        "optimizacion_pedidos": "id_optimizacion,id_pedido", "metricas_ejecucion": "id_metrica",
        "materiales_utilizados": "id_material_utilizado"}
    # Fixed internal identifiers, never supplied by HTTP or a test fixture.
    with engine.connect() as connection:
        rows = {}
        for table, key in tables.items():
            row_json = "to_jsonb(t)"
            if table == "optimizaciones" and not include_ff_request_key:
                row_json += " - 'ff_request_key'"  # Compare the same columns across the additive migration.
            rows[table] = connection.exec_driver_sql(
                f"SELECT coalesce(jsonb_agg({row_json} ORDER BY {key})::text, '[]') FROM {table} t"
            ).scalar_one()
        return rows
