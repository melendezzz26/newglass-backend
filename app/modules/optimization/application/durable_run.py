"""Atomic FF admission, independent of any response or browser connection."""
from .admission import OptimizerBusy
from .comparison_acceptance_v2 import AcceptComparisonV2, SnapshotConflict
from .errors import RunError
from .load_budget import check_selection


class AcceptDurableRun:
    def __init__(self, unit_of_work, authorize, *, queue_capacity):
        self.uow, self.authorize, self.queue_capacity = unit_of_work, authorize, queue_capacity

    def execute(self, order_ids, actor_id, key):
        ids = AcceptComparisonV2.validate_request(order_ids, actor_id, key)
        snapshot = AcceptComparisonV2(self.uow, self.authorize)
        check_selection(ids, snapshot.budget)
        for attempt in range(3):
            try:
                with self.uow() as tx:
                    tx.consistent_snapshot(serializable=True)
                    tx.comparisons_v2.lock_queue()
                    self.authorize(tx, actor_id)
                    previous = tx.runs.find_ff_request(actor_id, key)
                    if previous is not None:
                        if tx.runs.get(previous).result.problem.order_ids != ids:
                            raise RunError("STATE_CONFLICT", "La clave corresponde a otro lote.")
                        return previous, tx.worker_jobs.status(previous) or "RECOVERY_BLOCKED"
                    tx.worker_jobs.require_durable_ff()
                    waiting = tx.worker_jobs.waiting_count()
                    if waiting >= self.queue_capacity:
                        error = OptimizerBusy("queued_comparisons", self.queue_capacity, waiting)
                        error.details = {**error.details, "request_accepted": False}
                        raise error
                    frozen = snapshot.freeze(tx, ids)
                    run = tx.runs.create(frozen.problem, frozen.configuration_id, actor_id, request_key=key)
                    tx.worker_jobs.enqueue(run.optimization_id, frozen.problem, kind="FF")
                    tx.orders.transition(ids, "PENDIENTE", "EN_OPTIMIZACION")
                    return run.optimization_id, "WAITING"
            except SnapshotConflict:
                if attempt == 2:
                    raise
