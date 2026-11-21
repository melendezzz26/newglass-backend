"""Linux laboratory instrumentation. The measurement wraps the native FF call."""
import json
import os
from pathlib import Path
import time


def measured_engine(*args):
    from app.modules.optimization.domain.first_fit import FirstFitOptimizer
    from app.modules.optimization.infrastructure.sequential_worker import calculation_worker_v2

    if args[0]["method"] == "FF":
        original = FirstFitOptimizer.optimize
        folder = Path(os.environ["QA_LAB_FILES"])
        partners = set(filter(None, os.environ.get("QA_LAB_PARTNERS", "").split(",")))
        holds = set(filter(None, os.environ.get("QA_LAB_HOLDS", "").split(",")))

        def measure(self, problem, *values, **kwargs):
            identifier = str(problem.order_ids[0])
            check = kwargs["checkpoint"]
            if identifier in partners:
                (folder / f"ready-{identifier}").touch()
                while not all((folder / f"ready-{order}").exists() for order in partners):
                    check()
                    time.sleep(.002)
            start = time.monotonic_ns()
            cpu = time.thread_time_ns()
            result = original(self, problem, *values, **kwargs)
            cpu_ns, end = time.thread_time_ns() - cpu, time.monotonic_ns()
            sample = folder / f"motor-{identifier}.json"
            temporary = sample.with_suffix(".tmp")
            temporary.write_text(json.dumps(dict(start_ns=start, end_ns=end, cpu_ns=cpu_ns, pid=os.getpid(),
                                                 placed=result.placed_units, total=result.total_units)))
            temporary.replace(sample)
            if identifier in holds:
                while not (folder / f"resume-{identifier}").exists():
                    check()
                    time.sleep(.025)
            return result

        FirstFitOptimizer.optimize = measure
    calculation_worker_v2(*args)


def run():
    from functools import partial
    from app.modules.optimization import worker_v2
    from app.modules.optimization.infrastructure.sequential_runner import SequentialProcessRunner

    class MeasuredWorker(worker_v2.SeparatedComparisonWorker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, runner_factory=partial(SequentialProcessRunner, worker_target=measured_engine), **kwargs)

    worker_v2.SeparatedComparisonWorker = MeasuredWorker
    worker_v2.main()


if __name__ == "__main__":
    run()
