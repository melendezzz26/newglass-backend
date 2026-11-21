"""Linux cgroup v2 attestation and resource supervision for the same-host pilot."""
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import socket

from ..application.comparison_limits import MIB
from ..application.errors import RunError
from .process_lifecycle import process_scope


def counters(path):
    return {key: int(value) for key, value in (line.split() for line in path.read_text().splitlines())}


def pressure(path):
    first = path.read_text().splitlines()[0].split()
    return float(first[1].split("=", 1)[1])


@dataclass(frozen=True)
class ParallelLab:
    slice_name: str
    path: Path
    profile: dict

    @classmethod
    def discover(cls, slice_name):
        try:
            if not re.fullmatch(r"newglasslab-[a-z0-9]+\.slice", slice_name):
                raise ValueError("A dedicated laboratory systemd slice is required")
            relative = cls.membership()
            if relative.parts.count(slice_name) != 1 or relative.name == slice_name:
                raise ValueError("The worker service must belong to a child of the laboratory slice")
            location = Path(*relative.parts[:relative.parts.index(slice_name) + 1])
            path = Path("/sys/fs/cgroup") / location.relative_to("/")
            profile = cls.attest(path, location)
            lab = cls(slice_name, path, profile)
            lab.check()
            return lab
        except (OSError, ValueError, KeyError, IndexError) as error:
            raise RunError("RESOURCE_UNAVAILABLE", "El entorno Linux no acredita los límites del piloto paralelo.") from error

    @staticmethod
    def membership():
        for entry in Path("/proc/self/cgroup").read_text().splitlines():
            if entry.startswith("0::"):
                relative = Path(entry.removeprefix("0::"))
                if relative.is_absolute() and ".." not in relative.parts:
                    return relative
        raise ValueError("Cgroup v2 required")

    @staticmethod
    def attest(path, location):
        memory = int((path / "memory.max").read_text())
        cpu_quota, cpu_period = map(int, (path / "cpu.max").read_text().split())
        pids = int((path / "pids.max").read_text())
        mem_total = int(Path("/proc/meminfo").read_text().splitlines()[0].split()[1]) * 1024
        if (not 1536 * MIB <= memory <= 4096 * MIB or memory + 512 * MIB > mem_total
                or (path / "memory.swap.max").read_text().strip() != "0"
                or not 2 * cpu_period <= cpu_quota <= min(8, len(os.sched_getaffinity(0))) * cpu_period
                or not 24 <= pids <= 128):
            raise ValueError("Inadequate hard cgroup limits")
        root = Path("/sys/fs/cgroup")
        for parent in path.parents:
            if not parent.is_relative_to(root):
                break
            mem_limit = parent / "memory.max"
            if mem_limit.exists():
                limit = mem_limit.read_text().strip()
                if limit != "max" and int(limit) < memory:
                    raise ValueError("A parent cgroup imposes a smaller memory budget")
            cpu_limit = parent / "cpu.max"
            if cpu_limit.exists():
                quota, period = cpu_limit.read_text().split()
                if quota != "max" and int(quota) * cpu_period < cpu_quota * int(period):
                    raise ValueError("A parent cgroup imposes a smaller CPU budget")
            tasks_limit = parent / "pids.max"
            if tasks_limit.exists():
                limit = tasks_limit.read_text().strip()
                if limit != "max" and int(limit) < pids:
                    raise ValueError("A parent cgroup imposes a smaller task limit")
        # Each physical budget belongs to one attested namespace/cgroup on one boot.
        events = counters(path / "memory.events")
        return dict(scope=process_scope(), network=os.stat("/proc/self/ns/net").st_ino, uid=os.getuid(),
                    cgroup=str(location), inode=path.stat().st_ino, memory_bytes=memory,
                    cpu_quota=cpu_quota, cpu_period=cpu_period, pids_limit=pids,
                    oom=events["oom"], oom_kill=events["oom_kill"])

    def sample(self, *, include_processes=False):
        memory = {line.split()[0].rstrip(":"): int(line.split()[1])
                  for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemAvailable:")}
        events = counters(self.path / "memory.events")
        cpu = counters(self.path / "cpu.stat")
        sample = dict(memory_current=int((self.path / "memory.current").read_text()),
                    memory_peak=int((self.path / "memory.peak").read_text()),
                    pids_current=int((self.path / "pids.current").read_text()),
                    host_available=memory["MemAvailable"] * 1024,
                    host_cpu_pressure=pressure(Path("/proc/pressure/cpu")),
                    group_cpu_pressure=pressure(self.path / "cpu.pressure"),
                    group_cpu_usage_us=cpu["usage_usec"], group_cpu_throttled_us=cpu["throttled_usec"],
                    oom=events["oom"], oom_kill=events["oom_kill"])
        if include_processes:
            rss = {}
            for listing in self.path.rglob("cgroup.procs"):
                try:
                    pids = listing.read_text().split()
                except FileNotFoundError:
                    continue
                for pid in pids:
                    try:
                        rss[pid] = int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
                    except (FileNotFoundError, ProcessLookupError):
                        continue
            sample.update(group_rss_bytes=sum(rss.values()), group_processes=len(rss), largest_process_rss=max(rss.values(), default=0))
        return sample

    def check(self, *, admission=False):
        try:
            expected = Path(self.profile["cgroup"])
            if not self.membership().is_relative_to(expected) or self.attest(self.path, expected) != self.profile:
                raise ValueError("Membership, limits or OOM counters changed")
            sample = self.sample(include_processes=not admission)
            if (sample["memory_current"] * 100 >= self.profile["memory_bytes"] * (80 if admission else 90)
                    or sample["host_available"] < 256 * MIB
                    or sample["pids_current"] + (8 if admission else 1) >= self.profile["pids_limit"]
                    or admission and (sample["host_cpu_pressure"] >= 75 or sample["group_cpu_pressure"] >= 75)):
                raise ValueError("Laboratory resource pressure exceeds the permitted ceiling")
            return sample
        except (OSError, ValueError, KeyError, IndexError) as error:
            raise RunError("RESOURCE_UNAVAILABLE", "El piloto paralelo no tiene margen de recursos acreditado.") from error

    @contextmanager
    def lane(self, number):
        # The same network namespace is required by the attestation. A kernel lock
        # disappears on death and admits at most two local database pools.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as lock:
            try:
                lock.bind(f"\0newglass-lab-{self.profile['inode']}-{number}")
            except OSError as error:
                raise RunError("RESOURCE_UNAVAILABLE", "Ya hay un servicio worker inscrito con esa identidad.") from error
            yield
