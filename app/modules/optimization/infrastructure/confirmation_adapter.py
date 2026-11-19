"""Caller-owned PostgreSQL confirmation; no cross-module infrastructure imports.

Lock order: run -> FF execution -> associations -> Orders ASC -> sheets ASC ->
remnants ASC. Writes only flush; SqlRunUnitOfWork owns the SINGLE commit.
"""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select

from app.models import (Optimizacion, OptimizacionPedido, EjecucionOptimizacion, Plancha, Retazo,
                        MovimientoInventario, MaterialUtilizado, MetricaEjecucion)
from app.modules.inventory.domain.geometry import calculate_area_mm2
from app.modules.inventory.domain.exceptions import InvalidGeometryError
from app.shared.stock_protection import protected_units
from ..application.confirmation import ConfirmationContext, CreatedRemnant
from ..application.errors import RunError
from ..domain.metrics import ResidualRectangle
from ..domain.problem import MaterialSource
from ..domain.rotation import raster_number, RasterPreparationError
from .run_snapshot import decode_residual_policy, json_value


def result_after_confirmation(result, created_areas):
    metrics = result.metrics
    recovered = sum(created_areas, Decimal(0))
    if recovered == metrics.recoverable_remnant_mm2:
        return result
    return replace(result, metrics=replace(metrics, recoverable_remnant_mm2=recovered,
        waste_mm2=metrics.material_area_mm2 - metrics.placed_piece_area_mm2 - recovered))


def confirmed_residual_view(result, residuals, confirmation, *, versioned):
    created = {}
    for remnant in confirmation["receipt"]["remnants_created"]:
        source = remnant["source"]
        key = (source["source"], source["material_id"], source["physical_unit_index"])
        if versioned and key in created:
            raise ValueError("Confirmation receipt duplicates a physical source")
        created[key] = remnant
    persisted = []
    for residual in residuals:
        remnant = created.get((residual.material_source, residual.material_id, residual.stock_unit_index))
        if remnant is None:
            continue
        if versioned and (Decimal(remnant["x_mm"]), Decimal(remnant["y_mm"]), Decimal(remnant["exact_area_mm2"])) != (
                residual.x_mm, residual.y_mm, residual.area_mm2):
            raise ValueError("Confirmation receipt differs from measured residuals")
        persisted.append(residual)
    if versioned:
        if result is None or result.metrics is None or len(persisted) != len(created):
            raise ValueError("Confirmation receipt differs from measured residuals")
        result = result_after_confirmation(result, (r.area_mm2 for r in persisted))
    return result, tuple(persisted)


class SqlConfirmationRepository:
    def __init__(self, session, runs):
        self.session, self.runs = session, runs
        self.locked, self.skipped = {}, []
        self.method_name = "First Fit"

    def lock_run(self, optimization_id):
        row = self.session.scalar(select(Optimizacion).where(Optimizacion.id_optimizacion == optimization_id)
                                  .with_for_update().execution_options(populate_existing=True))
        if row is None:
            raise RunError("NOT_FOUND", "No se encontró la optimización.")
        execution = self.session.scalar(select(EjecucionOptimizacion).where(
            EjecucionOptimizacion.id_optimizacion == optimization_id, EjecucionOptimizacion.metodo == "FF")
            .with_for_update().execution_options(populate_existing=True))
        if execution is None:
            raise RunError("NOT_CONFIRMABLE", "No hay ejecución FF confirmable.")
        self.session.scalars(select(OptimizacionPedido).where(OptimizacionPedido.id_optimizacion == optimization_id)
                             .order_by(OptimizacionPedido.id_pedido).with_for_update()).all()
        # Fence an already consumed run even if its receipt/order states were altered externally.
        movement = self.session.scalar(select(MovimientoInventario.id_movimiento).where(
            MovimientoInventario.id_optimizacion == optimization_id,
            MovimientoInventario.tipo_evento == "CONSUMO_OPTIMIZACION").limit(1))
        if movement is not None:
            raise RunError("ALREADY_CONFIRMED", "La optimización ya fue confirmada.")
        if (row.estado != "COMPLETADA" or execution.estado != "COMPLETADA" or not execution.completo
                or row.id_ejecucion_seleccionada != execution.id_ejecucion):
            raise RunError("NOT_CONFIRMABLE", "No hay una ejecución FF completa seleccionada para confirmar.")
        if row.ff_request_key is not None:
            from .worker_jobs import WorkerJobs
            if not WorkerJobs(self.session).ff_closed_for_confirmation(optimization_id):
                raise RunError("NOT_CONFIRMABLE", "No se acreditó el cierre de la ejecución FF.")
        try:
            run = self.runs.get(optimization_id)
            snapshot = execution.patron_resultado
            residuals = tuple(ResidualRectangle(**{**r, **{k: Decimal(r[k]) for k in (
                "x_mm", "y_mm", "width_mm", "height_mm", "area_mm2")}}) for r in snapshot.get("residual_rectangles", []))
            minimum_width = Decimal(snapshot["residual_minimum_width_mm"])
            minimum_height = Decimal(snapshot["residual_minimum_height_mm"])
            raw_policy = snapshot.get("residual_policy")
            policy = decode_residual_policy(raw_policy) if raw_policy is not None else None
        except RunError as exc:
            if exc.code in ("INTERNAL_ERROR", "NOT_FOUND"):
                raise RunError("NOT_CONFIRMABLE", "El resultado persistido requiere revisión antes de confirmar.") from exc
            raise
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            raise RunError("NOT_CONFIRMABLE", "El resultado persistido no es confirmable.") from exc
        return ConfirmationContext(run=run, execution_id=execution.id_ejecucion,
            minimum_width_mm=minimum_width, minimum_height_mm=minimum_height, residuals=residuals,
            residual_policy=policy)

    def _lock_metric(self, context):
        metric = self.session.scalar(select(MetricaEjecucion).where(
            MetricaEjecucion.id_ejecucion == context.execution_id).with_for_update().execution_options(populate_existing=True))
        result = context.run.result
        metrics = result.metrics
        if metric is None or metrics is None:
            raise RunError("NOT_CONFIRMABLE", "No se encontraron las métricas de la solución elegida.")
        # NUMERIC(18,2) rounds the area projection independently of the exact JSON proof.
        for stored, exact in ((metric.area_material_total_mm2, metrics.material_area_mm2),
                              (metric.area_piezas_colocadas_mm2, metrics.placed_piece_area_mm2),
                              (metric.retazo_recuperable_mm2, metrics.recoverable_remnant_mm2),
                              (metric.merma_mm2, metrics.waste_mm2)):
            if stored != exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):
                raise RunError("NOT_CONFIRMABLE", "Las métricas persistidas difieren del resultado.")
        if (metric.aprovechamiento_pct, metric.planchas_nuevas_usadas, metric.tiempo_computacional_ms) != (
                metrics.utilization_pct, metrics.new_sheets_used, result.computation_time_ms):
            raise RunError("NOT_CONFIRMABLE", "Las métricas persistidas difieren del resultado.")
        return metric

    def lock_and_revalidate(self, consumptions, context):
        self.metric = self._lock_metric(context)
        for model, source, id_column in ((Plancha, MaterialSource.SHEET, Plancha.id_plancha),
                                         (Retazo, MaterialSource.REMNANT, Retazo.id_retazo)):
            selected = [c for c in consumptions if c.stock.source is source]
            ids = [c.stock.material_id for c in selected]
            if not ids:
                continue
            rows = self.session.scalars(select(model).where(id_column.in_(ids)).order_by(id_column)
                                        .with_for_update().execution_options(populate_existing=True)).all()
            by_id = {getattr(r, id_column.key): r for r in rows}
            reserved = protected_units(self.session, source.value, ids)
            for consumption in selected:
                stock = consumption.stock
                row = by_id.get(stock.material_id)
                compatible = row is not None and row.estado and (row.id_tipo_vidrio, row.espesor_mm) == stock.material_key
                if compatible and source is MaterialSource.SHEET:
                    compatible = (row.cantidad >= consumption.quantity
                        and row.ancho_mm == Decimal(str(stock.geometry['width_mm']))
                        and row.alto_mm == Decimal(str(stock.geometry['height_mm']))
                        and row.ancho_mm * row.alto_mm == stock.area_mm2)
                elif compatible:
                    compatible = row.geometria == json_value(stock.geometry) and row.area_mm2 == stock.area_mm2
                    # Boolean reactivation must NEVER allow a concrete remnant to be consumed twice.
                    previous = self.session.scalar(select(MovimientoInventario.id_movimiento).where(
                        MovimientoInventario.id_retazo == stock.material_id,
                        MovimientoInventario.tipo_evento == "CONSUMO_OPTIMIZACION").limit(1))
                    compatible = compatible and previous is None
                if not compatible:
                    raise RunError("STOCK_CONFLICT", "El stock utilizado cambió o ya no está disponible.")
                minimum = reserved.get(stock.material_id, 0)
                if minimum and (source is MaterialSource.REMNANT or row.cantidad - consumption.quantity < minimum):
                    raise RunError("STOCK_CONFLICT", "El stock utilizado está protegido por una optimización pendiente.")
                self.locked[(source, stock.material_id)] = row
        proposals = self.session.scalars(select(MaterialUtilizado).where(
            MaterialUtilizado.id_ejecucion == context.execution_id).order_by(MaterialUtilizado.id_material_utilizado)
            .with_for_update()).all()
        actual = {}
        for row in proposals:
            key = (MaterialSource.SHEET, row.id_plancha) if row.id_plancha is not None else (MaterialSource.REMNANT, row.id_retazo)
            actual[key] = actual.get(key, 0) + row.cantidad_utilizada
        expected = {(c.stock.source, c.stock.material_id): c.quantity for c in consumptions}
        if actual != expected:
            raise RunError("NOT_CONFIRMABLE", "Los materiales propuestos no corresponden al patrón persistido.")

    def consume(self, consumptions):
        for consumption in consumptions:
            stock = consumption.stock
            row = self.locked[(stock.source, stock.material_id)]
            if stock.source is MaterialSource.SHEET:
                row.cantidad -= consumption.quantity
                row.estado = row.cantidad > 0
            else:
                row.estado = False
        self.session.flush()

    def record_movements(self, consumptions, optimization_id, actor_id):
        for c in consumptions:
            sheet = c.stock.source is MaterialSource.SHEET
            self.session.add(MovimientoInventario(id_plancha=c.stock.material_id if sheet else None,
                id_retazo=None if sheet else c.stock.material_id, tipo_evento="CONSUMO_OPTIMIZACION",
                motivo=f"Confirmación {self.method_name} de optimización {optimization_id}.",
                observacion="stock_unit_index=" + ",".join(str(i) for i in c.unit_indices),
                id_usuario=actor_id, id_optimizacion=optimization_id, cantidad_afectada=c.quantity,
                fecha=datetime.now(timezone.utc)))
        self.session.flush()

    def create_residuals(self, residuals, context):
        stocks = {s.identity: s for s in context.run.result.materials_used}
        created = []
        for r in residuals:
            source = stocks[(MaterialSource(r.material_source), r.material_id, r.stock_unit_index)]
            try:
                geometry = dict(type="RECTANGULO", width_mm=raster_number(r.width_mm), height_mm=raster_number(r.height_mm))
                area = calculate_area_mm2(geometry)  # Public Inventory rule, including NUMERIC(18,2) precision.
            except (InvalidGeometryError, RasterPreparationError):
                self.skipped.append({"source": json_value(r), "reason": "INVENTORY_GEOMETRY_NOT_PERSISTABLE"})
                continue
            marker = 'P' if source.source is MaterialSource.SHEET else 'R'
            code = f"{context.run.result.method}-{context.run.optimization_id}-{marker}{source.material_id}-{source.physical_unit_index}"
            if self.session.scalar(select(Retazo.id_retazo).where(Retazo.codigo == code)) is not None:
                raise RunError("STATE_CONFLICT", "El código del retazo resultante ya existe; solicita revisión.")
            row = Retazo(codigo=code, id_tipo_vidrio=source.glass_type_id, espesor_mm=source.thickness_mm,
                geometria=geometry, area_mm2=area, estado=True, fecha_registro=datetime.now(timezone.utc),
                id_ejecucion_origen=context.execution_id)
            self.session.add(row)
            self.session.flush()
            created.append(CreatedRemnant(remnant_id=row.id_retazo, source=source, x_mm=r.x_mm, y_mm=r.y_mm,
                geometry=geometry, area_mm2=area, exact_area_mm2=r.area_mm2, codigo=row.codigo))
        return tuple(created)

    def mark_confirmed(self, context, receipt, actor_id):
        execution = self.session.get(EjecucionOptimizacion, context.execution_id)
        result = result_after_confirmation(context.run.result, (r.exact_area_mm2 for r in receipt.remnants_created))
        self.metric.retazo_recuperable_mm2 = result.metrics.recoverable_remnant_mm2
        self.metric.merma_mm2 = result.metrics.waste_mm2
        execution.patron_resultado = {**execution.patron_resultado, "confirmation": {
            "receipt_version": 1, "actor_id": actor_id, "confirmed_at": datetime.now(timezone.utc).isoformat(),
            "receipt": json_value(receipt), "skipped_residuals": self.skipped}}
