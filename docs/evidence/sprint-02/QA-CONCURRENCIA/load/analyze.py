"""Métricas ISO/IEC 25023 y gráficos de S2-QA-CONCURRENCIA a partir de los registros crudos.

Uso:  python -m experiments.load.analyze CARPETA_SALIDA
Lee CARPETA_SALIDA/resultados/{E1,E2,E3}/uNN_rR/ y escribe:
  resultados/resumen_E1.csv, resumen_E2.csv, e2_comparaciones.csv, resumen_E3.json,
  metricas_iso25023.csv/.json y graficos/*.png
Percentiles por rango más cercano: valor en la posición ceil(p·n) de los datos ordenados.
"""
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import statistics
import sys
import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OBJECTIVE_MS = 2000
THINK_MEAN_S = 0.6           # between(0.2, 1.0)
API_CPU_MAX = 80.0           # % de un núcleo
API_RSS_MAX = 512.0          # MiB, dyno Basic
WORKER_RSS_MAX = 448.0       # MiB, NEWGLASS_COMPARISON_AGGREGATE_MIB
CONTROLLED = {("503", "OPTIMIZER_BUSY"), ("429", "PROGRESS_BUSY")}

# Paleta de referencia validada (slots 1-3, todos los pares) y neutros de superficie clara.
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, INK2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
GOOD, CRITICAL = "#0ca30c", "#d03b3b"


def pct(values, p):
    data = sorted(values)
    if not data:
        return None
    return data[max(0, math.ceil(p * len(data)) - 1)]


def mean(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def peak(values):
    return max((v for v in values if v is not None), default=None)


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def runs(root, scenario):
    base = root / "resultados" / scenario
    if not base.exists():
        return []
    items = []
    for run_dir in sorted(base.iterdir()):
        if not (run_dir / "run.json").exists():
            continue
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        meta["dir"] = run_dir
        meta["phases"] = json.loads((run_dir / "phases.json").read_text(encoding="utf-8"))
        items.append(meta)
    return items


def classify(rows):
    failures = controlled = client_errors = 0
    for row in rows:
        status = row["status"]
        if (status, row["code"]) in CONTROLLED:
            controlled += 1
        elif status in ("-1", "0") or status.startswith("5"):
            failures += 1
        elif status.startswith("4"):
            client_errors += 1
    return failures, controlled, client_errors


def resources(run_dir, start, stop):
    rows = [r for r in read_csv(run_dir / "resources.csv") if start <= float(r["t_epoch_s"]) <= stop]
    result = {}
    for name in ("api", "worker"):
        cpu = [float(r[f"{name}_cpu_pct"]) for r in rows if r[f"{name}_cpu_pct"]]
        rss = [float(r[f"{name}_rss_mib"]) for r in rows if r[f"{name}_rss_mib"]]
        result[name] = dict(cpu_mean=statistics.fmean(cpu) if cpu else None, cpu_peak=max(cpu, default=None),
                            rss_mean=statistics.fmean(rss) if rss else None, rss_peak=max(rss, default=None),
                            samples=len(rows))
    return result


# ------------------------------------------------------------------ E1
def analyze_e1(root, scenarios=("E1", "E1R")):
    """E1R: 75 y 100 usuarios con pila nueva por repetición. E1S (aparte): 100 usuarios con ramp-up lento."""
    per_run, levels = [], {}
    for meta in [m for scenario in scenarios for m in runs(root, scenario)]:
        phases = meta["phases"]
        start, stop = phases["spawning_complete"], phases["test_stop"]
        rows = [r for r in read_csv(meta["dir"] / "requests.csv")
                if r["method"] == "GET" and start <= float(r["t_start_epoch_s"]) <= stop]
        times = [float(r["response_ms"]) for r in rows]
        window = stop - start
        failures, controlled, client_errors = classify(rows)
        res = resources(meta["dir"], start + 3, stop)  # sin el bcrypt de los últimos logins
        logins = [r for r in read_csv(meta["dir"] / "requests.csv") if r["name"] == "POST /api/auth/login"]
        recovery_path = meta["dir"] / "recovery.json"
        run = dict(users=meta["users"], rep=meta["repetition"], n=len(times), window_s=window,
                   logins_ok=sum(r["status"] == "200" for r in logins), logins_failed=sum(r["status"] != "200" for r in logins),
                   recovery=json.loads(recovery_path.read_text(encoding="utf-8")) if recovery_path.exists() else None,
                   mean=mean(times), p50=pct(times, .5), p95=pct(times, .95), p99=pct(times, .99),
                   throughput=len(times) / window, failures=failures, controlled=controlled,
                   client_errors=client_errors, api=res["api"], worker=res["worker"], times=times)
        per_run.append(run)
        levels.setdefault(meta["users"], []).append(run)
    summary = []
    for users in sorted(levels):
        reps = levels[users]
        pooled = [t for r in reps for t in r["times"]]
        total = sum(r["n"] for r in reps)
        row = dict(users=users, repetitions=len(reps), n=total, window_min_s=min(r["window_s"] for r in reps),
                   mean_ms=mean(pooled), p50_ms=pct(pooled, .5), p95_ms=pct(pooled, .95),
                   p99_ms=pct(pooled, .99), p95_rep_min=min((r["p95"] for r in reps if r["p95"] is not None), default=None),
                   p95_rep_max=peak(r["p95"] for r in reps),
                   logins_failed=sum(r["logins_failed"] for r in reps),
                   reps_failed=sum(1 for r in reps if r["failures"] or r["p95"] is None or r["p95"] > OBJECTIVE_MS),
                   recovered_65s=(sum(1 for r in reps if r["recovery"] and r["recovery"][-1]["status"] == 200)
                                  if any(r["recovery"] for r in reps) else None),
                   throughput_rps=statistics.fmean(r["throughput"] for r in reps),
                   throughput_min=min(r["throughput"] for r in reps), throughput_max=max(r["throughput"] for r in reps),
                   failures=sum(r["failures"] for r in reps), controlled=sum(r["controlled"] for r in reps),
                   client_errors=sum(r["client_errors"] for r in reps),
                   api_cpu_mean=mean(r["api"]["cpu_mean"] for r in reps),
                   api_cpu_peak=peak(r["api"]["cpu_peak"] for r in reps),
                   api_rss_mean=mean(r["api"]["rss_mean"] for r in reps),
                   api_rss_peak=peak(r["api"]["rss_peak"] for r in reps),
                   worker_cpu_mean=mean(r["worker"]["cpu_mean"] for r in reps),
                   worker_rss_peak=peak(r["worker"]["rss_peak"] for r in reps))
        row["failure_rate_pct"] = 100 * row["failures"] / total if total else 100.0
        row["adequacy_x"] = row["p95_ms"] / OBJECTIVE_MS if row["p95_ms"] is not None else float("inf")
        row["all_reps_ok"] = row["reps_failed"] == 0
        summary.append(row)
    one = next((r for r in summary if r["users"] == 1), None)
    for row in summary:
        ideal = row["users"] / (THINK_MEAN_S + (one["mean_ms"] if one else row["mean_ms"]) / 1000)
        row["ideal_rps"] = ideal
        row["efficiency"] = row["throughput_rps"] / ideal
    return summary, per_run


# ------------------------------------------------------------------ E2
def ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def analyze_e2(root, scenario="E2"):
    comparisons, summary = [], []
    by_level = {}
    for meta in runs(root, scenario):
        path = meta["dir"] / "e2_comparisons.jsonl"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        admitted = sorted([r for r in records if r["status"] == 201], key=lambda r: r["comparison_id"])
        for position, record in enumerate(admitted, 1):
            events = record["server_events"]
            record["position"] = position
            record["wait_s"] = ts(events["RUNNING"]) - ts(events["WAITING"]) if "RUNNING" in events else None
            record["total_s"] = ts(events["TERMINAL"]) - ts(events["WAITING"]) if "TERMINAL" in events else None
            record["running_epoch"] = ts(events["RUNNING"]) if "RUNNING" in events else None
            record["waiting_epoch"] = ts(events["WAITING"])
            record["terminal_epoch"] = ts(events["TERMINAL"]) if "TERMINAL" in events else None
        inversions = sum(1 for i, a in enumerate(admitted) for b in admitted[i + 1:]
                         if a["running_epoch"] and b["running_epoch"] and a["running_epoch"] > b["running_epoch"])
        rows = read_csv(meta["dir"] / "requests.csv")
        rows = [r for r in rows if r["method"] in ("GET", "POST", "PUT")]
        failures, controlled, client_errors = classify(rows)
        progress_429 = sum(1 for r in rows if r["status"] == "429" and "progress" in r["name"])
        retry_ok = all(r["retry_after"] for r in rows if (r["status"], r["code"]) in CONTROLLED)
        verify = json.loads((meta["dir"] / "verify.json").read_text(encoding="utf-8"))
        res = resources(meta["dir"], meta["phases"]["test_start"], meta["phases"]["test_stop"])
        complete = sum(1 for r in admitted if r.get("status_final") == "COMPLETADA" and r.get("executions_complete") == 6)
        auto = sum(1 for r in admitted if r.get("total_s") is not None and r.get("engines_finished") == 6)
        run = dict(users=meta["users"], rep=meta["repetition"], requests=len(records), admitted=len(admitted),
                   rejected=sum(1 for r in records if r["status"] == 503 and r["code"] == "OPTIMIZER_BUSY"),
                   other_status=sorted({r["status"] for r in records} - {201, 503}),
                   conflicts=sum(1 for r in records if r["status"] == 409),
                   rejected_retry_after=[r["retry_after"] for r in records if r["status"] == 503],
                   admission_ms=[r["admission_ms"] for r in records], inversions=inversions, complete=complete,
                   auto_advanced=auto, http_requests=len(rows), failures=failures, controlled=controlled,
                   client_errors=client_errors, progress_429=progress_429, retry_after_present=retry_ok,
                   released_ok=all(r.get("release_status") == 200 for r in admitted), verify_ok=verify["ok"],
                   api=res["api"], worker=res["worker"],
                   duration_s=meta["phases"]["test_stop"] - meta["phases"]["test_start"])
        by_level.setdefault(meta["users"], []).append(run)
        for record in records:
            comparisons.append(dict(users=meta["users"], rep=meta["repetition"], **{
                k: record.get(k) for k in ("user_index", "comparison_id", "status", "code", "admission_ms", "position",
                                           "wait_s", "total_s", "client_to_terminal_ms", "result_state",
                                           "executions_complete", "release_status", "waiting_epoch",
                                           "running_epoch", "terminal_epoch")}))
    for users in sorted(by_level):
        reps = by_level[users]
        admission = [t for r in reps for t in r["admission_ms"]]
        summary.append(dict(users=users, repetitions=len(reps), requests=sum(r["requests"] for r in reps),
                            admitted=sum(r["admitted"] for r in reps), rejected_503=sum(r["rejected"] for r in reps),
                            admitted_per_rep=[r["admitted"] for r in reps],
                            other_status=sorted({s for r in reps for s in r["other_status"]}),
                            conflicts_409=sum(r["conflicts"] for r in reps),
                            admission_n=len(admission), admission_mean_ms=statistics.fmean(admission),
                            admission_p50_ms=pct(admission, .5), admission_p95_ms=pct(admission, .95),
                            admission_max_ms=max(admission), adequacy_x=pct(admission, .95) / OBJECTIVE_MS,
                            fifo_inversions=sum(r["inversions"] for r in reps),
                            complete=sum(r["complete"] for r in reps), auto_advanced=sum(r["auto_advanced"] for r in reps),
                            http_requests=sum(r["http_requests"] for r in reps), failures=sum(r["failures"] for r in reps),
                            controlled=sum(r["controlled"] for r in reps), progress_429=sum(r["progress_429"] for r in reps),
                            retry_after_present=all(r["retry_after_present"] for r in reps),
                            released_ok=all(r["released_ok"] for r in reps), verify_ok=all(r["verify_ok"] for r in reps),
                            api_cpu_mean=statistics.fmean(r["api"]["cpu_mean"] for r in reps),
                            api_cpu_peak=max(r["api"]["cpu_peak"] for r in reps),
                            api_rss_peak=max(r["api"]["rss_peak"] for r in reps),
                            worker_cpu_mean=statistics.fmean(r["worker"]["cpu_mean"] for r in reps),
                            worker_cpu_peak=max(r["worker"]["cpu_peak"] for r in reps),
                            worker_rss_mean=statistics.fmean(r["worker"]["rss_mean"] for r in reps),
                            worker_rss_peak=max(r["worker"]["rss_peak"] for r in reps),
                            duration_mean_s=statistics.fmean(r["duration_s"] for r in reps)))
    return summary, comparisons


# ------------------------------------------------------------------ E3
def analyze_e3_initial(root):
    """Corrida inicial de E3 (20 repeticiones, antes del diagnóstico ampliado)."""
    items = runs(root, "E3_corrida_inicial")
    if not items:
        return None
    meta = items[0]
    verify = json.loads((meta["dir"] / "verify.json").read_text(encoding="utf-8"))
    rows = [r for r in read_csv(meta["dir"] / "requests.csv") if r["method"] in ("GET", "POST", "PUT")]
    failures, controlled, client_errors = classify(rows)
    bad = [r for r in verify["reps"] if sorted(r["statuses"]) != [200, 409]]
    return dict(repetitions=len(verify["reps"]), incorrect_responses=len(bad), http_requests=len(rows),
                failures=failures, anomalies=[{k: r[k] for k in ("rep", "comparison_id", "statuses", "codes",
                                                                 "movement_sheet_units", "movements", "order_state")}
                                              for r in bad],
                sheet_before=verify["sheet_before"], sheet_after=verify["sheet_after"])


def analyze_e3(root):
    items = runs(root, "E3")
    if not items:
        return None
    reps, times, rows, steps = [], [], [], []
    sheet = dict(before=None, after=None, delta=0, stored=0)
    api, worker = [], []
    for meta in items:
        verify = json.loads((meta["dir"] / "verify.json").read_text(encoding="utf-8"))
        reps += [dict(run=meta["repetition"], **r) for r in verify["reps"]]
        sheet["before"] = verify["sheet_before"] if sheet["before"] is None else sheet["before"]
        sheet["after"] = verify["sheet_after"]
        sheet["delta"] += verify["sheet_delta"]
        sheet["stored"] += verify["stored_sheet_units"]
        records = [json.loads(line) for line in
                   (meta["dir"] / "e3_confirmations.jsonl").read_text(encoding="utf-8").splitlines()]
        times += [r["response_ms"] for r in records]
        rows += [r for r in read_csv(meta["dir"] / "requests.csv") if r["method"] in ("GET", "POST", "PUT")]
        res = resources(meta["dir"], meta["phases"]["test_start"], meta["phases"]["test_stop"])
        api.append(res["api"])
        worker.append(res["worker"])
        steps += [float(r["clock_step_ms"]) for r in read_csv(meta["dir"] / "resources.csv") if r.get("clock_step_ms")]
    failures, controlled, client_errors = classify(rows)
    anomalies = [r for r in reps if not r["response_ok"]]
    return dict(runs=len(items), repetitions=len(reps), double_discounts=sum(r["double_discount"] for r in reps),
                incorrect_responses=len(anomalies),
                unreadable_after_confirm=sum(r["readback_status"] not in (200, None) for r in reps),
                anomalies=[{k: r[k] for k in ("run", "rep", "comparison_id", "statuses", "codes", "single_effect",
                                              "order_state", "readback_status", "confirmed_at",
                                              "supervisor_stopped_at", "confirmed_before_stop")} for r in anomalies],
                confirmed_before_stop=sum(bool(r["confirmed_before_stop"]) for r in reps),
                second_codes=sorted({c for r in reps for c in r["codes"] if c}),
                sheet_before=sheet["before"], sheet_after=sheet["after"], sheet_delta=sheet["delta"],
                stored_sheet_units=sheet["stored"],
                remnants_consumed=sum(len(r["stored_remnants_consumed"]) for r in reps),
                max_send_skew_ms=max(r["send_skew_ms"] for r in reps),
                orders_confirmed=sum(r["order_state"] == "CONFIRMADO" for r in reps),
                confirm_p50_ms=pct(times, .5), confirm_p95_ms=pct(times, .95), confirm_n=len(times),
                http_requests=len(rows), failures=failures, controlled=controlled, client_errors=client_errors,
                clock_step_range_ms=(min(steps), max(steps)) if steps else None,
                api_rss_peak=max(r["rss_peak"] for r in api), worker_rss_peak=max(r["rss_peak"] for r in worker),
                ok=sum(r["double_discount"] for r in reps) == 0 and sheet["delta"] == sheet["stored"])


# ------------------------------------------------------------------ métricas ISO
def fmt(value, digits=0):
    return "—" if value is None else f"{value:,.{digits}f}".replace(",", " ")


def verdict(ok):
    return "Cumple" if ok else "No cumple"


def iso_table(e1_all, e2, e3, e2b=None, e1s=None):
    rows = []
    e1 = [r for r in e1_all if r["users"] <= 50]   # niveles especificados en la SPEC
    top = e1[-1]
    rows.append(dict(id="PTb-1", medida="Tiempo medio de respuesta, lecturas (50 usuarios)",
                     formula="media = Σt/n; pXX = t[ceil(p·n)]",
                     valor=f"media {fmt(top['mean_ms'], 1)} · p50 {fmt(top['p50_ms'], 1)} · p95 {fmt(top['p95_ms'], 1)} · p99 {fmt(top['p99_ms'], 1)} ms (n={top['n']})",
                     umbral="Se informa; veredicto vía PTb-2", veredicto=verdict(top["adequacy_x"] <= 1) + " (vía PTb-2)"))
    worst = max(e1, key=lambda r: r["adequacy_x"])
    rows.append(dict(id="PTb-2", medida="Adecuación del tiempo de respuesta, lecturas",
                     formula="X = p95 / 2000 ms", valor=f"X máx = {worst['adequacy_x']:.3f} ({worst['users']} usuarios, p95 {fmt(worst['p95_ms'], 1)} ms)",
                     umbral="X ≤ 1 en cada nivel", veredicto=verdict(all(r["adequacy_x"] <= 1 for r in e1))))
    worst2 = max(e2, key=lambda r: r["adequacy_x"])
    rows.append(dict(id="PTb-2", medida="Adecuación del tiempo de respuesta, admisión de comparación",
                     formula="X = p95 admisión / 2000 ms",
                     valor=f"X máx = {worst2['adequacy_x']:.3f} ({worst2['users']} usuarios, p95 {fmt(worst2['admission_p95_ms'])} ms; n={worst2['admission_n']}, muestra insuficiente)",
                     umbral="X ≤ 1 en cada nivel", veredicto=verdict(all(r["adequacy_x"] <= 1 for r in e2))))
    least = min(e1, key=lambda r: r["efficiency"])
    rows.append(dict(id="PTb-5", medida="Rendimiento (throughput), lecturas",
                     formula="X = completadas / ventana; eficiencia = X / (N / (0,6 s + t̄₁))",
                     valor=f"{fmt(top['throughput_rps'], 1)} req/s con 50 usuarios; eficiencia mín {least['efficiency']:.2f} ({least['users']} usuarios)",
                     umbral="Eficiencia ≥ 0,90", veredicto=verdict(least["efficiency"] >= .9)))
    ok_levels = [r for r in e1_all if r["all_reps_ok"] and r["efficiency"] >= .9]
    pca1 = max(ok_levels, key=lambda r: r["throughput_rps"]) if ok_levels else None
    fifty = next((r for r in e1 if r["users"] == 50), None)
    beyond = [r for r in e1_all if r["users"] > 50]
    degraded = [r for r in e1_all if not (r["all_reps_ok"] and r["efficiency"] >= .9)]
    rows.append(dict(id="PCa-1", medida="Capacidad de procesamiento",
                     formula="máx PTb-5 en niveles con p95 ≤ objetivo, 0 % 5xx y eficiencia ≥ 0,90",
                     valor=(f"{fmt(pca1['throughput_rps'], 1)} req/s ({pca1['users']} usuarios)" if pca1 else "—")
                           + (f"; degrada desde {min(r['users'] for r in degraded)} usuarios" if degraded else
                              f"; sin degradación hasta {e1_all[-1]['users']} usuarios (incluye exploratorios)" if beyond else ""),
                     umbral="Sin degradación hasta 50 usuarios",
                     veredicto=verdict(bool(fifty) and fifty["all_reps_ok"] and fifty["efficiency"] >= .9)))
    passing = sorted(r["users"] for r in e1_all if r["all_reps_ok"])
    first_fail = min((r["users"] for r in e1_all if not r["all_reps_ok"]), default=None)
    pca2 = max((u for u in passing if first_fail is None or u < first_fail), default=0)
    rows.append(dict(id="PCa-2", medida="Capacidad de acceso de usuarios, lecturas",
                     formula="máx N con p95 ≤ 2000 ms y 0 % 5xx en las 3 repeticiones",
                     valor=((f"≥ {pca2} usuarios (máximo ensayado; sin fallo)" if first_fail is None
                             else f"{pca2} usuarios con ramp-up de N/10 usuarios/s (falla con {first_fail})")
                            + (f"; con ramp-up lento de 2 usuarios/s, 100 usuarios: "
                               f"{3 - e1s[0]['reps_failed']}/3 repeticiones cumplen, p95 {fmt(e1s[0]['p95_ms'], 1)} ms, "
                               f"{e1s[0]['failures']} × 5xx" if e1s else "")),
                     umbral="≥ 50 usuarios", veredicto=verdict(pca2 >= 50)))
    full = {r["users"]: r for r in e2}
    small = [u for u in (1, 2, 4, 8) if u in full]
    small_ok = all(full[u]["rejected_503"] == 0 and full[u]["admitted"] == 3 * u for u in small)
    conflicts = sum(r["conflicts_409"] for r in e2)
    ten = full.get(10)
    staggered = e2b[0] if e2b else None
    queue_ok = bool(staggered) and staggered["rejected_503"] > 0 and staggered["retry_after_present"]         and staggered["conflicts_409"] == 0
    rows.append(dict(id="PCa-cola", medida="Capacidad de la cola de comparaciones",
                     formula="admitidas (201), conflictos (409) y cola llena (503) por nivel",
                     valor=(f"Simultáneas 1–8: {sum(full[u]['admitted'] for u in small)}/{sum(3 * u for u in small)} admitidas, "
                            f"{conflicts} × 409 STATE_CONFLICT en E2; 10 simultáneas: admitidas {ten['admitted_per_rep'] if ten else '—'} "
                            f"y 0 × 503. Escalonadas (E2b, 10): admitidas {staggered['admitted_per_rep'] if staggered else '—'}, "
                            f"{staggered['rejected_503'] if staggered else 0} × 503 OPTIMIZER_BUSY"),
                     umbral="≤ 8 simultáneas: 100 % admitidas; exceso con 503 + Retry-After",
                     veredicto=verdict(small_ok and queue_ok)))
    cpu = max(r["api_cpu_mean"] for r in e1) if e1 else None
    rows.append(dict(id="PRu-1", medida="CPU de la API (lecturas)", formula="Δ CPU árbol / Δt × 100",
                     valor=f"media máx {fmt(cpu, 1)} % · pico {fmt(max(r['api_cpu_peak'] for r in e1), 1)} % de un núcleo",
                     umbral="Media ≤ 80 % de un núcleo", veredicto=verdict(cpu is not None and cpu <= API_CPU_MAX)))
    rows.append(dict(id="PRu-1", medida="CPU del worker y motores (comparaciones)", formula="Δ CPU árbol / Δt × 100",
                     valor=f"media {fmt(max(r['worker_cpu_mean'] for r in e2), 1)} % · pico {fmt(max(r['worker_cpu_peak'] for r in e2), 1)} %",
                     umbral="Se informa", veredicto="Informativa"))
    api_rss = max([r["api_rss_peak"] for r in e1] + [r["api_rss_peak"] for r in e2])
    worker_rss = max(r["worker_rss_peak"] for r in e2)
    rows.append(dict(id="PRu-2", medida="RSS pico de API y worker", formula="máx Σ RSS sincronizado del árbol",
                     valor=f"API {fmt(api_rss, 1)} MiB · worker+motores {fmt(worker_rss, 1)} MiB",
                     umbral="API ≤ 512 MiB; worker ≤ 448 MiB",
                     veredicto=verdict(api_rss <= API_RSS_MAX and worker_rss <= WORKER_RSS_MAX)))
    init = e3["initial"] if e3 else None
    total = (sum(r["n"] for r in e1) + sum(r["http_requests"] for r in e2 + (e2b or []))
             + (e3["http_requests"] if e3 else 0) + (init["http_requests"] if init else 0))
    fails = (sum(r["failures"] for r in e1) + sum(r["failures"] for r in e2 + (e2b or []))
             + (e3["failures"] if e3 else 0) + (init["failures"] if init else 0))
    overload = [r for r in e1_all if r["users"] > 50]
    rows.append(dict(id="RFa", medida="Tasa de fallos (5xx no controlados)", formula="5xx no controlados / total × 100",
                     valor=(f"Escenarios nominales (E1 ≤ 50, E2, E2b, E3 con su corrida inicial): {fails} de {total} = "
                            f"{100 * fails / total:.4f} %. Sobrecarga exploratoria (E1-R): "
                            + "; ".join(f"{r['users']} usuarios {r['failures']} de {r['n']} ({r['failure_rate_pct']:.1f} %)"
                                        for r in overload)),
                     umbral="0 % en escenarios nominales", veredicto=verdict(fails == 0)))
    e1_ctrl = sum(r["controlled"] for r in e1)
    e2_503 = sum(r["rejected_503"] for r in e2)
    e2_429 = sum(r["progress_429"] for r in e2)
    e2_total = sum(r["http_requests"] for r in e2)
    rows.append(dict(id="RRc", medida="Rechazos controlados", formula="(503 OPTIMIZER_BUSY + 429 PROGRESS_BUSY) / total × 100",
                     valor=f"E1 {e1_ctrl} (0 %); E2 {e2_503} × 503 y {e2_429} × 429 de {e2_total} = {100 * (e2_503 + e2_429) / e2_total:.2f} %",
                     umbral="E1 0 %; E2 solo 503 con 10 usuarios y 429 en /progress, con Retry-After",
                     veredicto=verdict(e1_ctrl == 0 and all(r["rejected_503"] == 0 for r in e2 if r["users"] <= 8)
                                       and all(r["retry_after_present"] for r in e2))))
    rows.append(dict(id="TQ-1", medida="Tiempo de espera en cola", formula="t(RUNNING) − t(WAITING) (PostgreSQL)",
                     valor="ver G4 y e2_comparaciones.csv", umbral="Se informa por posición", veredicto="Informativa"))
    rows.append(dict(id="TQ-2", medida="Tiempo total por comparación", formula="t(TERMINAL) − t(WAITING)",
                     valor="ver G4 y e2_comparaciones.csv", umbral="Se informa por posición", veredicto="Informativa"))
    queue_levels = e2 + (e2b or [])
    inversions = sum(r["fifo_inversions"] for r in queue_levels)
    admitted = sum(r["admitted"] for r in queue_levels)
    auto = sum(r["auto_advanced"] for r in queue_levels)
    rows.append(dict(id="RFt-1", medida="Orden FIFO y avance automático", formula="inversiones; avanzados / admitidos",
                     valor=f"{inversions} inversiones; {auto}/{admitted} avanzaron solos (E2 + E2b)",
                     umbral="0 inversiones y 100 %", veredicto=verdict(inversions == 0 and auto == admitted)))
    e3_total = (e3["repetitions"] + (init["repetitions"] if init else 0)) if e3 else 0
    complete = sum(r["complete"] for r in queue_levels) + e3_total
    started = admitted + e3_total
    rows.append(dict(id="MP-05", medida="Comparaciones completas", formula="COMPLETADA con 6 motores / admitidas × 100",
                     valor=f"{complete}/{started} = {100 * complete / started:.1f} % (E2, E2b y E3)", umbral="100 %", veredicto=verdict(complete == started)))
    if e3:
        rows.append(dict(id="RIn-1", medida="Integridad de la confirmación concurrente",
                         formula="repeticiones cuyo efecto en stock/movimientos difiere de UNA confirmación",
                         valor=(f"{e3['double_discounts']} de {e3['repetitions']} descuentos dobles; planchas descontadas "
                                f"Δ {e3['sheet_delta']} = {e3['stored_sheet_units']} de los recibos persistidos "
                                f"({e3['runs']} corridas, dos clústeres que parten de 40)"),
                         umbral="0 descuentos dobles", veredicto=verdict(e3["ok"])))
        n_all = e3["repetitions"] + (init["repetitions"] if init else 0)
        bad_all = e3["incorrect_responses"] + (init["incorrect_responses"] if init else 0)
        rows.append(dict(id="RFa-conf", medida="Respuesta correcta a la confirmación concurrente",
                         formula="repeticiones con exactamente una 200 y una 409 sin efecto / total",
                         valor=(f"{n_all - bad_all} de {n_all} ({100 * (n_all - bad_all) / n_all:.1f} %). Corrida inicial: "
                                f"{init['incorrect_responses'] if init else 0} de {init['repetitions'] if init else 0} con 500 + 422 "
                                f"y la confirmación aplicada una vez; corridas de diagnóstico: {e3['incorrect_responses']} de "
                                f"{e3['repetitions']}, {e3['unreadable_after_confirm']} ilegibles después"),
                         umbral="100 % de respuestas correctas", veredicto=verdict(bad_all == 0)))
    return rows, pca2


# ------------------------------------------------------------------ gráficos
def style(ax, title, xlabel, ylabel):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=INK, fontsize=12, pad=10)
    ax.set_xlabel(xlabel, color=INK2)
    ax.set_ylabel(ylabel, color=INK2)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=MUTED)


def figure(width=8, height=4.6, cols=1, rows=1):
    fig, axes = plt.subplots(rows, cols, figsize=(width, height), facecolor=SURFACE)
    return fig, axes


def save(fig, path, note):
    fig.text(0.01, 0.01, note, color=MUTED, fontsize=7.5)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def user_axis(ax, users):
    """Eje de usuarios en escala log; sombrea los niveles exploratorios (> 50)."""
    ax.set_xscale("log")
    ax.set_xticks(users, [str(u) for u in users])
    ax.minorticks_off()
    ax.set_xlim(users[0] * 0.8, users[-1] * 2.2)
    if users[-1] > 50:
        ax.axvspan(60, users[-1] * 2.2, color=GRID, alpha=.35, linewidth=0)
        ax.annotate("exploratorio (E1-R)", (63, 1), xycoords=("data", "axes fraction"), xytext=(0, -14),
                    textcoords="offset points", color=MUTED, fontsize=8)


def label_end(ax, x, y, text, color=INK2, dy=0):
    ax.annotate(text, (x, y), xytext=(6, dy), textcoords="offset points", color=color, fontsize=8.5, va="center")


def plots(root, e1, e2, comparisons, iso, env, e2b=None, comparisons_b=()):
    out = root / "graficos"
    out.mkdir(exist_ok=True)
    note = f"NewGlass {env.get('backend_sha', '')[:7]} · WSL2 loopback, 1 API + 1 worker · S2-QA-CONCURRENCIA"
    users = [r["users"] for r in e1]

    fig, ax = figure()
    for key, color, marker, name in (("p50_ms", AQUA, "s", "p50"), ("p95_ms", BLUE, "o", "p95"),
                                     ("p99_ms", ORANGE, "^", "p99")):
        values = [r[key] if r[key] is not None else float("nan") for r in e1]
        ax.plot(users, values, color=color, linewidth=2, marker=marker, markersize=7, label=name)
    last = e1[-1]
    label_end(ax, users[-1], last["p95_ms"], f"{users[-1]} usuarios: p95 ≈ {last['p95_ms'] / 1000:.0f} s", color=INK2, dy=-14)
    if len(e1) > 1:
        prev = e1[-2]
        label_end(ax, users[-2], prev["p95_ms"], f"p95 {prev['p95_ms']:.0f} ms", color=INK2, dy=-12)
    for r in e1:
        if r["p95_rep_min"] is not None:
            ax.vlines(r["users"], r["p95_rep_min"], r["p95_rep_max"], color=BLUE, linewidth=4, alpha=.25)
    ax.axhline(OBJECTIVE_MS, color=CRITICAL, linewidth=1.2, linestyle="--")
    label_end(ax, users[0], OBJECTIVE_MS, "objetivo p95 = 2000 ms", color=CRITICAL, dy=8)
    ax.set_yscale("log")
    user_axis(ax, users)
    style(ax, "E1 · Tiempo de respuesta de lecturas por usuarios simultáneos", "usuarios simultáneos",
          "tiempo de respuesta (ms, escala log)")
    ax.legend(frameon=False, loc="center left")
    save(fig, out / "G1_e1_latencia_p95.png", note + " · franja: rango de p95 entre las 3 repeticiones")

    fig, ax = figure()
    tp = [r["throughput_rps"] for r in e1]
    ax.plot(users, [r["ideal_rps"] for r in e1], color=MUTED, linewidth=1.5, linestyle="--", label="demanda ofrecida")
    ax.plot(users, tp, color=BLUE, linewidth=2, marker="o", markersize=7, label="medido (media de 3)")
    for r in e1:
        ax.vlines(r["users"], r["throughput_min"], r["throughput_max"], color=BLUE, linewidth=4, alpha=.25)
        label_end(ax, r["users"], r["throughput_rps"], f"{r['throughput_rps']:.0f} req/s · ef. {r['efficiency']:.2f}", dy=-10)
    user_axis(ax, users)
    style(ax, "E1 · Throughput de lecturas por usuarios simultáneos", "usuarios simultáneos", "peticiones por segundo")
    ax.legend(frameon=False, loc="upper left")
    save(fig, out / "G2_e1_throughput.png", note + " · demanda ofrecida = N / (0,6 s + tiempo medio con 1 usuario)")

    fig, (a, b) = figure(11, 4.6, cols=2)
    a.plot(users, [r["failure_rate_pct"] for r in e1], color=ORANGE, linewidth=2, marker="o", markersize=7, label="5xx no controlados")
    a.plot(users, [100 * r["controlled"] / r["n"] for r in e1], color=BLUE, linewidth=2, marker="s", markersize=7,
           linestyle=":", label="rechazos controlados")
    user_axis(a, users)
    a.set_ylim(-0.5, max(5, max(r["failure_rate_pct"] for r in e1) * 1.3))
    style(a, "E1 · Errores de lectura", "usuarios simultáneos", "% de peticiones")
    a.legend(frameon=False)
    groups = [(str(r["users"]), r) for r in e2] + [(f"{r['users']} escal.", r) for r in (e2b or [])]
    series = (("admitted", BLUE, "201 admitida"), ("conflicts_409", ORANGE, "409 STATE_CONFLICT"),
              ("rejected_503", AQUA, "503 OPTIMIZER_BUSY"), ("progress_429", YELLOW, "429 PROGRESS_BUSY (/progress)"))
    width = .2
    for k, (key, color, name) in enumerate(series):
        offset = (k - 1.5) * width
        values = [r[key] for _, r in groups]
        b.bar([i + offset for i in range(len(groups))], values, width - .03, color=color, label=name)
        for i, value in enumerate(values):
            b.annotate(str(value), (i + offset, value), xytext=(0, 2), textcoords="offset points", ha="center",
                       fontsize=7, color=INK2)
    b.set_xticks(range(len(groups)), [g for g, _ in groups])
    style(b, "E2 · Respuestas por nivel (3 repeticiones)", "usuarios lanzando a la vez", "cantidad")
    b.legend(frameon=False, fontsize=7.5, loc="upper left")
    save(fig, out / "G3_errores_y_rechazos.png", note + " · 0 fallos 5xx no controlados en E2: ver tabla de métricas")

    fig, panels = figure(12, 4.8, cols=2)
    for ax, data, title in ((panels[0], comparisons, "E2 · envíos simultáneos (1–10 usuarios)"),
                            (panels[1], comparisons_b, "E2b · 10 envíos escalonados cada 400 ms")):
        admitted = [c for c in data if c["status"] == 201 and c["wait_s"] is not None]
        if not admitted:
            ax.axis("off")
            continue
        positions = sorted({c["position"] for c in admitted})
        ax.scatter([c["position"] - .08 for c in admitted], [c["wait_s"] for c in admitted], s=22, color=BLUE,
                   alpha=.45, edgecolors=SURFACE, linewidths=1)
        ax.scatter([c["position"] + .08 for c in admitted], [c["total_s"] for c in admitted], s=22, color=ORANGE,
                   alpha=.45, marker="^", edgecolors=SURFACE, linewidths=1)
        mean_wait = [statistics.fmean(c["wait_s"] for c in admitted if c["position"] == p) for p in positions]
        mean_total = [statistics.fmean(c["total_s"] for c in admitted if c["position"] == p) for p in positions]
        ax.plot(positions, mean_wait, color=BLUE, linewidth=2, label="espera en WAITING")
        ax.plot(positions, mean_total, color=ORANGE, linewidth=2, label="total hasta TERMINAL")
        label_end(ax, positions[-1], mean_wait[-1], f"espera {mean_wait[-1]:.1f} s")
        label_end(ax, positions[-1], mean_total[-1], f"total {mean_total[-1]:.1f} s")
        ax.set_xticks(positions)
        ax.set_xlim(.5, positions[-1] + 2.2)
        style(ax, title, "posición en la cola (orden de admisión)", "segundos (instantes de PostgreSQL)")
        ax.legend(frameon=False, loc="upper left")
    save(fig, out / "G4_e2_cola_por_posicion.png", note + " · puntos: cada comparación; línea: media por posición")

    levels = [r["users"] for r in e2]
    fig, axes = figure(11, 7.5, cols=2, rows=2)
    (c1, r1), (c2, r2) = axes
    c1.plot(users, [r["api_cpu_mean"] for r in e1], color=BLUE, linewidth=2, marker="o", markersize=7, label="media")
    c1.plot(users, [r["api_cpu_peak"] for r in e1], color=ORANGE, linewidth=2, marker="^", markersize=7, linestyle=":", label="pico (200 ms)")
    c1.axhline(API_CPU_MAX, color=CRITICAL, linestyle="--", linewidth=1.2)
    label_end(c1, users[0], API_CPU_MAX, "umbral media 80 %", color=CRITICAL, dy=8)
    user_axis(c1, users)
    style(c1, "E1 · CPU de la API", "usuarios simultáneos", "% de un núcleo")
    c1.legend(frameon=False, loc="upper left", bbox_to_anchor=(0, .9))
    r1.plot(users, [r["api_rss_mean"] for r in e1], color=BLUE, linewidth=2, marker="o", markersize=7, label="media")
    r1.plot(users, [r["api_rss_peak"] for r in e1], color=ORANGE, linewidth=2, marker="^", markersize=7, linestyle=":", label="pico")
    r1.axhline(API_RSS_MAX, color=CRITICAL, linestyle="--", linewidth=1.2)
    label_end(r1, users[0], API_RSS_MAX, "umbral 512 MiB", color=CRITICAL, dy=-8)
    r1.set_ylim(0, API_RSS_MAX * 1.1)
    user_axis(r1, users)
    style(r1, "E1 · RSS de la API", "usuarios simultáneos", "MiB")
    r1.legend(frameon=False, loc="center right")
    c2.plot(levels, [r["worker_cpu_mean"] for r in e2], color=BLUE, linewidth=2, marker="o", markersize=7, label="worker media")
    c2.plot(levels, [r["worker_cpu_peak"] for r in e2], color=ORANGE, linewidth=2, marker="^", markersize=7, linestyle=":", label="worker pico")
    c2.plot(levels, [r["api_cpu_mean"] for r in e2], color=AQUA, linewidth=2, marker="s", markersize=7, label="API media")
    c2.set_xticks(levels)
    style(c2, "E2 · CPU del worker (y motores) y de la API", "usuarios lanzando a la vez", "% de un núcleo")
    c2.legend(frameon=False, fontsize=8, loc="center right")
    r2.plot(levels, [r["worker_rss_mean"] for r in e2], color=BLUE, linewidth=2, marker="o", markersize=7, label="worker media")
    r2.plot(levels, [r["worker_rss_peak"] for r in e2], color=ORANGE, linewidth=2, marker="^", markersize=7, linestyle=":", label="worker pico")
    r2.plot(levels, [r["api_rss_peak"] for r in e2], color=AQUA, linewidth=2, marker="s", markersize=7, label="API pico")
    r2.axhline(WORKER_RSS_MAX, color=CRITICAL, linestyle="--", linewidth=1.2)
    label_end(r2, levels[0], WORKER_RSS_MAX, "umbral worker 448 MiB", color=CRITICAL, dy=-8)
    r2.set_ylim(0, WORKER_RSS_MAX * 1.15)
    r2.set_xticks(levels)
    style(r2, "E2 · RSS del worker (y motores) y de la API", "usuarios lanzando a la vez", "MiB")
    r2.legend(frameon=False, fontsize=8, loc="center right")
    save(fig, out / "G5_cpu_ram_por_usuarios.png", note + " · muestreo psutil cada 200 ms; RSS sumado puede contar páginas compartidas")

    timeline = [c for c in comparisons_b if c["users"] == 10 and c["rep"] == 1]
    if timeline:
        fig, ax = figure(9, 4.8)
        t0 = min(c["waiting_epoch"] for c in timeline if c["waiting_epoch"]) if any(c["waiting_epoch"] for c in timeline) else 0
        ordered = sorted(timeline, key=lambda c: (c["status"] != 201, c["comparison_id"] or 0))
        for row, c in enumerate(ordered):
            y = len(ordered) - row
            if c["status"] == 201:
                ax.barh(y, c["running_epoch"] - c["waiting_epoch"], left=c["waiting_epoch"] - t0, height=.6, color=BLUE, alpha=.35)
                ax.barh(y, c["terminal_epoch"] - c["running_epoch"], left=c["running_epoch"] - t0, height=.6, color=BLUE)
            else:
                sent = (c["user_index"] or 0) * 0.4      # E2b: envío escalonado cada 400 ms
                ax.plot([sent], [y], marker="X", markersize=11, color=ORANGE, markeredgecolor=SURFACE, markeredgewidth=1.5)
                ax.annotate("503 OPTIMIZER_BUSY · Retry-After (cola llena: 1 en ejecución + 8 en espera)", (sent, y),
                            xytext=(10, 0), textcoords="offset points", color=INK2, fontsize=8.5, va="center")
        ax.set_yticks(range(1, len(ordered) + 1),
                      [f"#{c['comparison_id']}" if c["status"] == 201 else "rechazada" for c in reversed(ordered)])
        style(ax, "E2b · Cola FIFO con 10 envíos escalonados (repetición 1)", "segundos desde la primera admisión", "comparación")
        ax.grid(axis="x", color=GRID, linewidth=.8)
        ax.set_ylim(.4, len(ordered) + .6)
        from matplotlib.lines import Line2D
        from matplotlib.patches import Patch
        ax.legend(handles=[Patch(color=BLUE, alpha=.35, label="WAITING"), Patch(color=BLUE, label="RUNNING (seis motores)"),
                           Line2D([], [], marker="X", color=ORANGE, linestyle="", markersize=9, label="rechazada")],
                  frameon=False, loc="upper right")
        save(fig, out / "G7_e2b_linea_de_tiempo_cola_llena.png", note + " · instantes del journal de progreso en PostgreSQL")

    widths = [8, 30, 62, 30, 13]                      # caracteres por columna
    header = ["ID", "Medida ISO/IEC 25023", "Valor medido", "Umbral", "Veredicto"]
    cells, heights = [], []
    for r in iso:
        mark = "✔ " if r["veredicto"].startswith("Cumple") else "✖ " if r["veredicto"].startswith("No") else "• "
        row = [r["id"], r["medida"], r["valor"], r["umbral"], mark + r["veredicto"]]
        wrapped = [textwrap.fill(str(v), w) for v, w in zip(row, widths)]
        cells.append(wrapped)
        heights.append(max(len(v.splitlines()) for v in wrapped))
    line = 0.155                                      # pulgadas por línea de texto
    fig, ax = figure(14, 0.9 + line * (sum(heights) + len(heights) + 1.5))
    ax.axis("off")
    table = ax.table(cellText=cells, colLabels=header, colWidths=[w / sum(widths) for w in widths],
                     loc="upper left", cellLoc="left", bbox=[0, 0, 1, 1])
    table.auto_set_font_size(False)
    table.set_fontsize(7.5)
    total = sum(heights) + len(heights) + 1.5
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        cell.set_facecolor(SURFACE)
        cell.set_height(((heights[row - 1] + 1) if row else 1.5) / total)
        cell.PAD = 0.02
        if row == 0:
            cell.set_text_props(color=INK, weight="bold")
        elif col == 4:
            text = cell.get_text().get_text()
            color = GOOD if text.startswith("✔") else CRITICAL if text.startswith("✖") else INK2
            cell.set_text_props(color=color, weight="bold")
    ax.set_title("Métricas ISO/IEC 25023 · concurrencia de NewGlass", loc="left", color=INK, fontsize=12)
    save(fig, out / "G6_tabla_metricas_iso25023.png", note)


def write_csv(path, rows):
    rows = [{k: v for k, v in r.items() if not isinstance(v, (list, dict))} for r in rows]
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    root = Path(sys.argv[1])
    env_path = root / "resultados" / "environment.json"
    env = json.loads(env_path.read_text(encoding="utf-8")) if env_path.exists() else {}
    e1, _ = analyze_e1(root)
    e1s, _ = analyze_e1(root, ("E1S",))
    e2, comparisons = analyze_e2(root)
    e2b, comparisons_b = analyze_e2(root, "E2b")
    e3 = analyze_e3(root)
    if e3:
        e3["initial"] = analyze_e3_initial(root)
    iso, pca2 = iso_table(e1, e2, e3, e2b, e1s)
    write_csv(root / "resultados" / "resumen_E1.csv", e1)
    write_csv(root / "resultados" / "resumen_E1S.csv", e1s)
    write_csv(root / "resultados" / "resumen_E2.csv", e2)
    write_csv(root / "resultados" / "resumen_E2b.csv", e2b)
    write_csv(root / "resultados" / "e2b_comparaciones.csv", comparisons_b)
    write_csv(root / "resultados" / "e2_comparaciones.csv", comparisons)
    (root / "resultados" / "resumen_E3.json").write_text(json.dumps(e3, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv(root / "resultados" / "metricas_iso25023.csv", iso)
    (root / "resultados" / "metricas_iso25023.json").write_text(
        json.dumps(dict(pca2=pca2, metricas=iso, e1=e1, e1s=e1s, e2=e2, e2b=e2b, e3=e3), indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    plots(root, e1, e2, comparisons, iso, env, e2b, comparisons_b)
    for row in iso:
        print(f"{row['id']:9} {row['veredicto']:22} {row['valor']}")


if __name__ == "__main__":
    main()
