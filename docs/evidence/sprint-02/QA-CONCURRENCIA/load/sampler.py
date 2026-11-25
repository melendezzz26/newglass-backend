"""PRu-1/PRu-2: CPU y RSS de la API y del worker (con descendientes) cada 200 ms.

CPU: contador acumulado del árbol = (usuario + sistema) de los procesos vivos + tiempo
de los hijos ya recolectados por la raíz (children_user/children_system). Su variación
entre dos muestras dividida por el intervalo es el % de CPU (100 % = un núcleo).
RSS: suma sincronizada del RSS de los procesos vivos del árbol (puede contar dos veces
páginas compartidas y perder picos entre muestras, igual que en la evidencia de Andro).
"""
import csv
import threading
import time

import psutil

INTERVAL_S = 0.2


class TreeCounter:
    def __init__(self, pid):
        self.root = psutil.Process(pid)
        self.processes = {pid: self.root}

    def sample(self):
        try:
            children = self.root.children(recursive=True)
        except psutil.NoSuchProcess:
            return None
        for child in children:
            self.processes.setdefault(child.pid, child)
        live = [self.root, *children]
        cpu = rss = 0.0
        count = 0
        for process in live:
            try:
                with process.oneshot():
                    times = process.cpu_times()
                    rss += process.memory_info().rss
                cpu += times.user + times.system
                if process is self.root:
                    cpu += times.children_user + times.children_system
                count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return cpu, rss / 2**20, count


class ResourceSampler:
    """Hilo que escribe una fila por muestra: t, cpu %, RSS MiB y procesos por árbol."""

    def __init__(self, targets: dict, path):
        self.targets = {name: TreeCounter(pid) for name, pid in targets.items()}
        self.path = path
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="resource-sampler", daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self):
        names = list(self.targets)
        # clock_step_ms: diferencia acumulada entre el reloj de pared y el monótono desde el inicio.
        # Un cambio brusco revela un salto del reloj de pared (sincronización de hora de WSL2).
        header = ["t_epoch_s", "clock_step_ms"] + [f"{n}_{k}" for n in names for k in ("cpu_pct", "rss_mib", "processes")]
        previous = {}
        with open(self.path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            next_tick = time.monotonic()
            origin = (time.time(), time.monotonic())
            while not self._stop.is_set():
                now_wall, now = time.time(), time.monotonic()
                step = ((now_wall - origin[0]) - (now - origin[1])) * 1000
                row = [f"{now_wall:.3f}", f"{step:.1f}"]
                for name in names:
                    sample = self.targets[name].sample()
                    if sample is None:
                        row += ["", "", ""]
                        continue
                    cpu, rss, count = sample
                    last = previous.get(name)
                    pct = "" if last is None else f"{max(0.0, (cpu - last[0]) / (now - last[1]) * 100):.1f}"
                    previous[name] = (cpu, now)
                    row += [pct, f"{rss:.2f}", count]
                writer.writerow(row)
                handle.flush()
                next_tick += INTERVAL_S
                self._stop.wait(max(0.0, next_tick - time.monotonic()))
