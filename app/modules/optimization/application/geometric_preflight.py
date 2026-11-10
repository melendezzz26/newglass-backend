"""Read-only validity of frozen v2 inputs; feasibility belongs to the engines."""
from app.modules.inventory.domain.geometry import calculate_area_mm2
from threading import Lock

from ..domain.problem import MaterialSource
from ..domain.rotation import RasterCache, RasterPreparationError, discrete_angles, raster_number
from .load_budget import DEFAULT_RUN_LOAD_BUDGET, check_raster_bases
from .errors import RunError
from ..domain.packing import _empty_layout
from ..domain.run_geometry import prepare_run_geometry

_FEASIBILITY_LOCK = Lock()


def validate_comparison_geometry(problem, budget=DEFAULT_RUN_LOAD_BUDGET):
    """Reuse preparation/Inventory rules without masks, positions or fit decisions.

    Orders already validates requested contours in prepare_persisted_problem.
    Inventory owns the stricter remnant contour contract. Check all extents
    before its quadratic polygon validation. Repeated physical units need only
    one check per source; nothing is normalized back into the frozen snapshot.
    """
    resolution = problem.config.resolution_mm
    raster_number(resolution)
    discrete_angles(problem.config.angular_step_deg)
    cache = RasterCache()
    pieces = {unit.piece_id: unit for unit in problem.units}
    stocks = {(stock.source, stock.material_id): stock
              for group in problem.groups for stock in group.stock_candidates}
    for item in (*pieces.values(), *stocks.values()):
        cache.check_base_geometry(item.geometry, resolution)
    for stock in stocks.values():
        if stock.source is MaterialSource.REMNANT:
            calculate_area_mm2(stock.geometry)
    check_raster_bases((piece.geometry for piece in pieces.values()), problem.config, budget,
                       stock=(stock.geometry for stock in stocks.values()))


def validate_individual_feasibility(problem, *, budget=DEFAULT_RUN_LOAD_BUDGET, max_checks=200_000):
    """Check individual placement only; an accepted batch may still be impossible.

    Search and masks use the same geometry, margin and orientation rules as the
    engines. Exhausting the work limit is not evidence of geometric impossibility.
    """
    if not _FEASIBILITY_LOCK.acquire(blocking=False):
        raise RunError('RESOURCE_UNAVAILABLE', 'La comprobacion geometrica esta ocupada; reintenta.')
    try:
        return _validate_individual_feasibility(problem, budget=budget, max_checks=max_checks)
    finally:
        _FEASIBILITY_LOCK.release()


def _validate_individual_feasibility(problem, *, budget, max_checks):
    validate_comparison_geometry(problem, budget)
    for group in problem.groups:
        if not group.stock_candidates:
            raise RunError('NO_COMPATIBLE_STOCK', 'No hay material compatible disponible para las piezas seleccionadas.')

    try:
        return _search_individual_feasibility(problem, max_checks)
    except RasterPreparationError as error:
        raise RunError('RESOURCE_UNAVAILABLE', 'No se pudo completar la comprobacion geometrica de forma segura.') from error


def _search_individual_feasibility(problem, max_checks):

    checks = 0

    def checkpoint():
        nonlocal checks
        checks += 1
        if checks > max_checks:
            raise RunError('RESOURCE_UNAVAILABLE', 'No se pudo completar la comprobacion geometrica dentro del limite seguro.')

    prepared = prepare_run_geometry(problem, checkpoint=checkpoint)
    for group in problem.groups:
        pieces = {(unit.order_id, unit.piece_id): unit for unit in group.units}
        sources = {(stock.source, stock.material_id): stock for stock in group.stock_candidates}
        for unit in pieces.values():
            fits = False
            for stock in sources.values():
                checkpoint()
                layout = _empty_layout(prepared.material(stock), problem.config)
                for oriented in prepared.orientations(unit):
                    checkpoint()
                    if layout._position(oriented.orientation.mask, checkpoint=checkpoint) is not None:
                        fits = True
                        break
                if fits:
                    break
            if not fits:
                raise RunError('PIECE_DOES_NOT_FIT', 'Una pieza no cabe en ningun material compatible.')
