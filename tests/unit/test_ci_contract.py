"""Contrato local del CI: gates y publicación solo desde integración."""
from pathlib import Path

import pytest
import yaml

from tests.integration.postgres_support import temporary_postgres

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("required,ci", [("1", "false"), ("0", "true")])
def test_missing_postgres_fails_when_required(tmp_path, monkeypatch, required, ci):
    monkeypatch.setenv("NEWGLASS_TEST_PG_BIN", str(tmp_path / "missing"))
    monkeypatch.setenv("NEWGLASS_REQUIRE_POSTGRES", required)
    monkeypatch.setenv("CI", ci)
    with pytest.raises(pytest.fail.Exception, match="PostgreSQL obligatorio"):
        with temporary_postgres(tmp_path):
            pytest.fail("No debe iniciar sin binarios")


def workflow(name):
    # BaseLoader conserva la clave YAML 'on' (sin coerción booleana YAML 1.1).
    return yaml.load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_backend_ci_retains_resource_sensitive_suites_and_isolation():
    ci = workflow("ci.yml")
    assert {"pull_request", "push", "workflow_call"} <= ci["on"].keys()
    assert ci["on"]["push"]["branches"] == ["main"]
    assert ci["permissions"] == {"contents": "read"}
    assert set(ci["jobs"]) == {"backend"}
    job = ci["jobs"]["backend"]
    assert job["env"]["NEWGLASS_REQUIRE_POSTGRES"] == "1"
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "pg_config --bindir" in commands
    assert "initdb" in commands and "pg_ctl" in commands
    assert "python -m pytest tests \\" in commands
    for suite in (
        "tests/integration/optimization/test_comparison_calculation.py",
        "tests/integration/optimization/test_comparison_supervision_v2.py",
        "tests/unit/optimization/test_sequential_runner.py",
        "tests/unit/optimization/test_comparison_runner.py",
    ):
        assert f"python -m pytest {suite} -q -ra" in commands
        assert f"--ignore={suite}" in commands
    raw = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "continue-on-error" not in raw
    assert "|| true" not in raw
    assert "--deselect" not in raw
    assert "secrets." not in raw
    assert "DATABASE_URL:" not in raw
    assert (ROOT / ".github/scripts/backend_failure_summary.py").is_file()


def test_procfile_runs_a_single_uvicorn_worker():
    """GAP-03 (SPEC-TA-037): la admisión y el supervisor de comparison-v2 viven en memoria.

    Heroku define WEB_CONCURRENCY y uvicorn lo toma como --workers si el Procfile no lo fija.
    """
    lines = (ROOT / "Procfile").read_text(encoding="utf-8").splitlines()
    web = [line for line in lines if line.startswith("web:")]
    assert len(web) == 1
    uvicorn = web[0].split("&&")[-1].split()
    assert uvicorn[:3] == ["python", "-m", "uvicorn"]
    assert [arg for arg in uvicorn if arg.startswith("--workers")] == ["--workers"]
    assert uvicorn[uvicorn.index("--workers") + 1] == "1"
