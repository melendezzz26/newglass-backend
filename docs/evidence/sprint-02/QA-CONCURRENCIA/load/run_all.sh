#!/usr/bin/env bash
# S2-QA-CONCURRENCIA: copia el backend (un commit fijo, solo lectura) al disco Linux de
# WSL2, agrega este arnés en la copia y ejecuta los escenarios con Locust contra
# servicios en 127.0.0.1. No modifica el repositorio, no usa .env ni bases remotas.
#
# Variables (todas opcionales):
#   BACKEND_SRC  checkout de newglass-backend      (por defecto: la repo que contiene este script;
#                si el script está fuera de una repo, ../../newglass-backend)
#   BACKEND_REF  commit medido                     (por defecto aa6883f, main del 25/11/2026)
#   QA_HOME      carpeta de trabajo en Linux       (por defecto ~/newglass-qa/conc)
#   PG_ROOT      raíz con usr/lib/postgresql/16/bin (por defecto ~/newglass-qa/pg; si no, pg_config)
#   VENV         entorno virtual de pruebas        (por defecto $QA_HOME/.venv)
#   OUT          salida                            (por defecto $QA_HOME/out)
# Argumentos: se pasan a run_load.py (por ejemplo --scenarios E1 o --levels 1,2 --reps 1).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOP="$(git -C "$HERE" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "${BACKEND_SRC:-}" ]; then
  if [ -n "$TOP" ] && [ -f "$TOP/main.py" ] && [ -d "$TOP/app" ]; then BACKEND_SRC="$TOP"; else BACKEND_SRC="$HERE/../../newglass-backend"; fi
fi
BACKEND_REF="${BACKEND_REF:-aa6883f888a2fa43629ed9d7f6110ca3645887e4}"
QA_HOME="${QA_HOME:-$HOME/newglass-qa/conc}"
PG_ROOT="${PG_ROOT:-$HOME/newglass-qa/pg}"
VENV="${VENV:-$QA_HOME/.venv}"
OUT="${OUT:-$QA_HOME/out}"

if [ -x "$PG_ROOT/usr/lib/postgresql/16/bin/initdb" ]; then
  export NEWGLASS_TEST_PG_BIN="$PG_ROOT/usr/lib/postgresql/16/bin"
  export LD_LIBRARY_PATH="$PG_ROOT/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
else
  export NEWGLASS_TEST_PG_BIN="${NEWGLASS_TEST_PG_BIN:-$(pg_config --bindir)}"
fi

# Copia del commit medido (git archive solo lee el repositorio).
rm -rf "$QA_HOME/backend"
mkdir -p "$QA_HOME/backend/experiments/load"
git -C "$BACKEND_SRC" archive "$BACKEND_REF" | tar -x -C "$QA_HOME/backend"
SHA="$(git -C "$BACKEND_SRC" rev-parse "$BACKEND_REF")"
cp "$HERE"/*.py "$QA_HOME/backend/experiments/load/"

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install -q -r "$QA_HOME/backend/requirements.txt" -r "$HERE/requirements-locust.txt"
fi

unset DATABASE_URL SECRET_KEY
export PYTHON_DOTENV_DISABLED=1
cd "$QA_HOME/backend"
"$VENV/bin/python" -m experiments.load.run_load --out "$OUT" --sha "$SHA" "$@"
"$VENV/bin/python" -m experiments.load.analyze "$OUT"
echo "Resultados en $OUT/resultados y gráficos en $OUT/graficos"
