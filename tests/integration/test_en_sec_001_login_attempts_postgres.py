"""EN-SEC-001 sobre un PostgreSQL temporal propio: migración y atomicidad real.

En Windows sin binarios PostgreSQL se omite; el CI la ejecuta (NEWGLASS_REQUIRE_POSTGRES=1).
"""
import os
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.modules.authentication.domain.login_attempts import (
    AttemptKey,
    AttemptPolicy,
    register_failure,
)
from app.modules.authentication.infrastructure.sqlalchemy_login_attempt_store import (
    SqlAlchemyLoginAttemptStore,
)
from tests.integration.postgres_support import BACKEND, _run, new_database, upgrade
from tests.model_contracts import current_metadata

HEAD = "s2conc01b210"
PREVIOUS = "s2cmp02d4e6f8"
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def downgrade(engine, target):
    assert engine.url.host == "127.0.0.1" and engine.url.username == "r1_test"
    environment = {**os.environ, "DATABASE_URL": engine.url.render_as_string(hide_password=False),
                   "PYTHONIOENCODING": "utf-8"}
    return _run([sys.executable, "-B", "-m", "alembic", "downgrade", target], cwd=BACKEND, env=environment)


def test_migration_matches_the_orm_and_downgrades_cleanly(isolated_postgres):
    with new_database(isolated_postgres) as engine:
        result = upgrade(engine, "head")
        assert result.returncode == 0, result.stderr
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == HEAD
            inspector = sa.inspect(connection)
            assert inspector.get_pk_constraint("intentos_login")["constrained_columns"] == ["alcance", "clave"]
            assert {c["name"] for c in inspector.get_check_constraints("intentos_login")} == {
                "ck_intentos_login_alcance", "ck_intentos_login_fallos"}
            assert [i["name"] for i in inspector.get_indexes("intentos_login")] == [
                "ix_intentos_login_actualizado_en"]
            context = MigrationContext.configure(
                connection, opts={"compare_type": True, "compare_server_default": True})
            assert compare_metadata(context, current_metadata()) == []

        result = downgrade(engine, PREVIOUS)
        assert result.returncode == 0, result.stderr
        with engine.connect() as connection:
            assert "intentos_login" not in sa.inspect(connection).get_table_names()

        result = upgrade(engine, "head")
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("alcance,fallos,constraint", [
    ("otro", 0, "ck_intentos_login_alcance"),
    ("ip", -1, "ck_intentos_login_fallos"),
])
def test_check_constraints_reject_invalid_rows(isolated_postgres, alcance, fallos, constraint):
    with new_database(isolated_postgres) as engine:
        assert upgrade(engine, "head").returncode == 0
        with pytest.raises(IntegrityError) as caught:
            with engine.begin() as connection:
                connection.execute(sa.text(
                    "INSERT INTO intentos_login(alcance, clave, fallos, actualizado_en) "
                    "VALUES (:alcance, '203.0.113.1', :fallos, now())"), {"alcance": alcance, "fallos": fallos})
        assert caught.value.orig.diag.constraint_name == constraint


def test_concurrent_failures_from_several_processes_are_never_lost(isolated_postgres):
    """Ocho sesiones (como varios procesos web) fallan a la vez con la misma clave."""
    with new_database(isolated_postgres) as engine:
        assert upgrade(engine, "head").returncode == 0
        factory = sessionmaker(bind=engine)
        policy = AttemptPolicy(max_failures=10_000, window=timedelta(hours=1), lockout=timedelta(minutes=1))
        key = AttemptKey.ip("203.0.113.77")
        workers, per_worker = 8, 10
        barrier = threading.Barrier(workers)
        errors = []

        def work():
            try:
                with factory() as db:
                    store = SqlAlchemyLoginAttemptStore(db)
                    barrier.wait()
                    for _ in range(per_worker):
                        store.update(key, lambda state: register_failure(state, NOW, policy), NOW)
            except Exception as error:  # noqa: BLE001 - se informa abajo
                errors.append(error)

        threads = [threading.Thread(target=work) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert errors == []
        with factory() as db:
            assert SqlAlchemyLoginAttemptStore(db).get(key).failures == workers * per_worker
