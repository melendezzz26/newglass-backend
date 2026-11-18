"""Intentos fallidos de inicio de sesión en la tabla intentos_login (EN-SEC-001)."""
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app.models import IntentoLogin
from app.modules.authentication.domain.login_attempts import AttemptKey, AttemptState


def _aware(value: datetime | None) -> datetime | None:
    # PostgreSQL devuelve fechas con zona; SQLite (pruebas) las devuelve sin
    # zona aunque se guardaron en UTC. El dominio siempre compara en UTC.
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _state(row: IntentoLogin) -> AttemptState:
    return AttemptState(
        failures=row.fallos,
        window_started_at=_aware(row.inicio_ventana),
        locked_until=_aware(row.bloqueado_hasta),
    )


def _same_key(key: AttemptKey):
    return (IntentoLogin.alcance == key.scope.value) & (IntentoLogin.clave == key.value)


class SqlAlchemyLoginAttemptStore:
    """Implementa LoginAttemptStore. Cada escritura confirma su propia transacción.

    Usa la sesión de la petición: antes de escribir solo se hicieron lecturas
    (cuenta y estado de intentos), así que confirmar no arrastra otros cambios.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, key: AttemptKey) -> AttemptState | None:
        row = self._db.execute(select(IntentoLogin).where(_same_key(key))).scalar_one_or_none()
        return None if row is None else _state(row)

    def update(
        self,
        key: AttemptKey,
        change: Callable[[AttemptState | None], AttemptState],
        now: datetime,
    ) -> AttemptState:
        try:
            # 1) La fila existe sí o sí (INSERT ... ON CONFLICT DO NOTHING), sin
            #    carrera entre procesos que fallan a la vez con la misma clave.
            self._db.execute(self._insert_if_absent(key, now))
            # 2) Bloqueo de fila: otro proceso espera hasta que confirmemos, así
            #    ningún fallo concurrente se pierde (lectura-modificación-escritura).
            #    populate_existing: si get() ya cargó la fila, se relee de la base
            #    en vez de usar la copia en memoria de la sesión.
            row = self._db.execute(
                select(IntentoLogin)
                .where(_same_key(key))
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one()
            new_state = change(_state(row))
            row.fallos = new_state.failures
            row.inicio_ventana = new_state.window_started_at
            row.bloqueado_hasta = new_state.locked_until
            row.actualizado_en = now
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return new_state

    def clear(self, key: AttemptKey) -> None:
        self._write(delete(IntentoLogin).where(_same_key(key)))

    def purge(self, older_than: datetime, now: datetime) -> None:
        self._write(
            delete(IntentoLogin).where(
                IntentoLogin.actualizado_en < older_than,
                or_(IntentoLogin.bloqueado_hasta.is_(None), IntentoLogin.bloqueado_hasta <= now),
            )
        )

    def _write(self, statement) -> None:
        try:
            self._db.execute(statement)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def _insert_if_absent(self, key: AttemptKey, now: datetime):
        values = {
            "alcance": key.scope.value,
            "clave": key.value,
            "fallos": 0,
            "actualizado_en": now,
        }
        dialect = self._db.get_bind().dialect.name
        if dialect == "postgresql":
            insert = postgresql.insert
        elif dialect == "sqlite":
            insert = sqlite.insert
        else:
            raise RuntimeError(f"Dialecto no soportado para intentos de login: {dialect}")
        return insert(IntentoLogin).values(**values).on_conflict_do_nothing(
            index_elements=["alcance", "clave"]
        )
