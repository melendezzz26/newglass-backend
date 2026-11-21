from pathlib import Path
import sys
from uuid import uuid4

import pytest

from app.modules.optimization.application.errors import RunError
from app.modules.optimization.infrastructure.parallel_lab import ParallelLab


@pytest.fixture
def lab(monkeypatch):
    profile = {"cgroup": "/newglasslab-qa.slice", "memory_bytes": 1000, "pids_limit": 64, "oom": 0, "oom_kill": 0}
    sample = dict(memory_current=700, host_available=1024**3, pids_current=9, host_cpu_pressure=0, group_cpu_pressure=0)
    attest = dict(profile)
    monkeypatch.setattr(ParallelLab, "membership", staticmethod(lambda: Path(profile["cgroup"]) / "lane.scope"))
    monkeypatch.setattr(ParallelLab, "attest", staticmethod(lambda *_: attest))
    monkeypatch.setattr(ParallelLab, "sample", lambda self, **kwargs: sample)
    return ParallelLab("newglasslab-qa.slice", Path("/unused"), profile), sample, attest


def test_pressure_defers_admission_before_it_stops_active_work(lab):
    policy, sample, _ = lab
    assert policy.check(admission=True) == sample
    sample["memory_current"] = 840
    with pytest.raises(RunError):
        policy.check(admission=True)
    assert policy.check() == sample
    sample["memory_current"] = 910
    with pytest.raises(RunError):
        policy.check()


def test_oom_or_cpu_saturation_are_distinct(lab):
    policy, sample, attestation = lab
    sample["group_cpu_pressure"] = 80
    with pytest.raises(RunError):
        policy.check(admission=True)
    assert policy.check() == sample
    attestation["oom_kill"] = 1
    with pytest.raises(RunError):
        policy.check()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux abstract sockets")
def test_each_lane_admits_at_most_one_worker():
    lab = ParallelLab("unused", Path("/unused"), {"inode": uuid4().int % 2**64})
    with lab.lane(1), lab.lane(2):
        with pytest.raises(RunError):
            with lab.lane(1):
                pytest.fail("The kernel lock admitted a duplicate worker")
    with lab.lane(1):
        pass
