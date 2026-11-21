"""Frozen stock references shared by warehouse and confirmation transactions."""
from sqlalchemy import func, select


def protected_units(session, source, material_ids):
    from app.models import OptimizationStockProtection as Protection

    # References to the same lot protect the same numbered candidate units.
    # They are not independent consumptions, so take the maximum, not the sum.
    return dict(session.execute(select(Protection.material_id, func.max(Protection.units)).where(
        Protection.source == source, Protection.material_id.in_(material_ids)
    ).group_by(Protection.material_id)).all())
