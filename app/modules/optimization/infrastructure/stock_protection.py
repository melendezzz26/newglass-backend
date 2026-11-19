"""Keep frozen inventory references valid while an external job owns its reservation."""
from decimal import Decimal

from sqlalchemy import delete, select

from app.models import OptimizationStockProtection as Protection, Plancha, Retazo
from ..application.errors import RunError
from ..domain.problem import MaterialSource
from .run_snapshot import json_value


def protect_stock(session, job_id, problem):
    for source, model, key in ((MaterialSource.SHEET, Plancha, Plancha.id_plancha),
                               (MaterialSource.REMNANT, Retazo, Retazo.id_retazo)):
        candidates = {}
        for group in problem.groups:
            for stock in group.stock_candidates:
                if stock.source is source and (stock.material_id not in candidates or
                        stock.physical_unit_index > candidates[stock.material_id].physical_unit_index):
                    candidates[stock.material_id] = stock
        if not candidates:
            continue
        # All inventory writers take the same row locks. SHARE allows other snapshots.
        rows = session.scalars(select(model).where(key.in_(candidates)).order_by(key).with_for_update(read=True)).all()
        if len(rows) != len(candidates):
            raise RunError("STOCK_CONFLICT", "El inventario cambió durante la admisión de la optimización.")
        for row in rows:
            stock = candidates[getattr(row, key.key)]
            valid = row.estado and (row.id_tipo_vidrio, row.espesor_mm) == stock.material_key
            if source is MaterialSource.SHEET:
                valid = (valid and row.cantidad >= stock.physical_unit_index
                    and row.ancho_mm == Decimal(str(stock.geometry["width_mm"]))
                    and row.alto_mm == Decimal(str(stock.geometry["height_mm"])))
            else:
                valid = valid and row.geometria == json_value(stock.geometry) and row.area_mm2 == stock.area_mm2
            if not valid:
                raise RunError("STOCK_CONFLICT", "El inventario cambió durante la admisión de la optimización.")
            session.add(Protection(job_id=job_id, source=source.value, material_id=stock.material_id,
                                   units=stock.physical_unit_index))


def release_stock(session, job_id):
    session.execute(delete(Protection).where(Protection.job_id == job_id))
