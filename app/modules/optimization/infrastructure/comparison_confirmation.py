"""Confirmar exclusivamente el resultado elegido; reusar revalidación de stock.

Autor: Fabricio Melendez Quezada. Revisor asignado: Andro Quispe.
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from app.models import EjecucionOptimizacion, MovimientoInventario, OptimizacionPedido
from ..application.confirmation import ConfirmationContext, ConfirmationResult, CreatedRemnant
from ..application.dto import OptimizationRun, RunStatus
from ..application.errors import RunError
from ..domain.problem import StockCandidate, MaterialSource
from .confirmation_adapter import SqlConfirmationRepository, result_after_confirmation
from .run_snapshot import decode_residual_policy, json_value


def decode_receipt(value):
    def stock(values):
        return StockCandidate(**{**values, "source": MaterialSource(values["source"]),
            "thickness_mm": Decimal(values["thickness_mm"]), "area_mm2": Decimal(values["area_mm2"])})
    created = tuple(CreatedRemnant(**{**r, "source": stock(r["source"]),
        **{k: Decimal(r[k]) for k in ("x_mm", "y_mm", "area_mm2", "exact_area_mm2")}})
        for r in value["remnants_created"])
    return ConfirmationResult(optimization_id=value["optimization_id"], order_ids=tuple(value["order_ids"]),
        materials_consumed=tuple(stock(s) for s in value["materials_consumed"]), remnants_created=created)


class SqlComparisonConfirmationRepository(SqlConfirmationRepository):
    method_names = {"FF": "First Fit", "BF": "Best Fit", "WF": "Worst Fit"}

    def __init__(self, session, comparisons):
        super().__init__(session, None)
        self.comparisons = comparisons

    def confirmation_view(self, row, actor_id):
        if row.id_usuario_ejecutor != actor_id:
            raise RunError("NOT_FOUND", "No se encontró la comparación.")
        return self.comparisons.get(row.id_optimizacion)

    def residual_policy(self, execution, stored):
        value = stored.get("residual_policy")
        return decode_residual_policy(value) if value is not None else None

    def lock_comparison(self, comparison_id, execution_id, expected_version, key, actor_id):
        row = self.comparisons._lock(comparison_id)
        try:
            current = self.confirmation_view(row, actor_id)
        except RunError as error:
            if error.code != "INTERNAL_ERROR":
                raise
            raise RunError("NOT_CONFIRMABLE", "El patrón persistido requiere revisión. Ejecuta una nueva comparación.") from error
        confirmation = current.confirmation
        if confirmation is not None:
            if (confirmation.get("key") == key and confirmation.get("execution_id") == execution_id
                    and confirmation.get("expected_version") == expected_version):
                try:
                    if type(confirmation.get("receipt_version")) is not int or confirmation["receipt_version"] != 1:
                        raise ValueError("Unsupported confirmation receipt")
                    receipt = decode_receipt(confirmation["receipt"])
                    if receipt.optimization_id != comparison_id or receipt.order_ids != current.problem.order_ids:
                        raise ValueError("Receipt differs from accepted orders")
                    return receipt
                except (ValueError, KeyError, TypeError, ArithmeticError) as error:
                    raise RunError("NOT_CONFIRMABLE", "El recibo persistido requiere revisión.") from error
            raise RunError("ALREADY_CONFIRMED", "Esta comparación ya fue confirmada.")
        if row.comparison_version != expected_version:
            raise RunError("STATE_CONFLICT", "La comparación o su selección cambió. Actualiza el resultado.")
        execution = next((e for e in current.executions if e.execution_id == execution_id), None)
        if (not current.terminal or current.released or current.selected_execution_id != execution_id
                or execution is None or not execution.eligible):
            raise RunError("NOT_CONFIRMABLE", "Selecciona un resultado completo y guardado antes de confirmar.")
        locked = self.comparisons._execution(comparison_id, execution_id)
        self.session.scalars(select(OptimizacionPedido).where(OptimizacionPedido.id_optimizacion == comparison_id)
            .order_by(OptimizacionPedido.id_pedido).with_for_update()).all()
        if self.session.scalar(select(MovimientoInventario.id_movimiento).where(
                MovimientoInventario.id_optimizacion == comparison_id,
                MovimientoInventario.tipo_evento == "CONSUMO_OPTIMIZACION").limit(1)) is not None:
            raise RunError("ALREADY_CONFIRMED", "La comparación ya tiene consumos registrados.")
        self.key, self.expected_version = key, expected_version
        self.method_name = self.method_names[execution.method]
        run = OptimizationRun(optimization_id=comparison_id, status=RunStatus.COMPLETED,
                              result=execution.result, residuals=execution.residuals)
        try:
            policy = self.residual_policy(execution, locked.patron_resultado)
        except (ValueError, KeyError, TypeError, ArithmeticError) as error:
            raise RunError("NOT_CONFIRMABLE", "La política del resultado requiere revisión. Ejecuta una nueva comparación.") from error
        return ConfirmationContext(run=run, execution_id=execution_id, minimum_width_mm=current.minimum_width_mm,
            minimum_height_mm=current.minimum_height_mm, residuals=execution.residuals, residual_policy=policy)

    def mark_confirmed(self, context, receipt, actor_id):
        execution = self.session.get(EjecucionOptimizacion, context.execution_id)
        result = result_after_confirmation(context.run.result, (r.exact_area_mm2 for r in receipt.remnants_created))
        self.metric.retazo_recuperable_mm2 = result.metrics.recoverable_remnant_mm2
        self.metric.merma_mm2 = result.metrics.waste_mm2
        row = self.comparisons._lock(context.run.optimization_id)
        confirmation = {
            "receipt_version": 1, "key": self.key, "expected_version": self.expected_version,
            "execution_id": context.execution_id, "method": result.method, "actor_id": actor_id,
            "confirmed_at": datetime.now(timezone.utc).isoformat(), "receipt": json_value(receipt),
            "skipped_residuals": self.skipped}
        # Inventory consulta el origen por ejecución; conservar ese contrato de lectura.
        execution.patron_resultado = {**execution.patron_resultado, "confirmation": confirmation}
        row.comparison_snapshot = {**row.comparison_snapshot, "confirmation": confirmation}
        row.comparison_version += 1


class SqlComparisonV2ConfirmationRepository(SqlComparisonConfirmationRepository):
    from ..application.comparison_catalog import CATALOG_V2
    method_names = {definition.code: definition.name for definition in CATALOG_V2}

    def confirmation_view(self, row, actor_id):
        if row.id_usuario_ejecutor != actor_id:
            raise RunError("NOT_FOUND", "No se encontró la comparación.")
        if row.comparison_snapshot.get("profile") != "comparison-v2":
            raise RunError("COMPARISON_PROFILE_MISMATCH", "El recurso pertenece a otro perfil de optimización.")
        current = self.comparisons.get(row.id_optimizacion)
        if current.cancellation is not None and current.cancellation["status"] == "CANCELLED":
            raise RunError("NOT_CONFIRMABLE", "El trabajo fue cancelado; no se pueden confirmar cortes.")
        if not current.terminal or not current.supervisor_stopped:
            raise RunError("NOT_CONFIRMABLE", "La comparación todavía no acredita cierre seguro.")
        return current

    def residual_policy(self, execution, stored):
        return execution.residual_policy
