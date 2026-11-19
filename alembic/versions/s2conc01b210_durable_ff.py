"""EN-CONC-001: admit standalone FF to the shared worker queue."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "s2conc01b210"
down_revision = "s2conc01b200"
branch_labels = None
depends_on = None

TABLE = "optimization_worker_attempts"
JOB = "optimization_worker_jobs"
KIND = "ck_optimization_worker_job_kind"
DISPATCH = "ck_optimization_worker_attempt_dispatch"


def upgrade():
    op.add_column("optimizaciones", sa.Column("ff_request_key", sa.String(64)))
    op.create_unique_constraint("uq_optimizaciones_ff_request_key", "optimizaciones",
                                ["id_usuario_ejecutor", "ff_request_key"])
    op.create_check_constraint("ck_optimizaciones_ff_request", "optimizaciones",
        "ff_request_key IS NULL OR (comparison_key IS NULL AND criterio_seleccion_version = 'S1-FF-v1')")
    op.drop_constraint(KIND, JOB, type_="check")
    op.create_check_constraint(KIND, JOB, "kind IN ('FF', 'V1', 'V2') AND (kind <> 'V1' OR state <> 'WAITING')")
    op.add_column(TABLE, sa.Column("dispatch_at", sa.DateTime(timezone=True)))
    op.add_column(TABLE, sa.Column("worker_identity", JSONB(none_as_null=True)))
    op.create_check_constraint(DISPATCH, TABLE, "worker_identity IS NULL OR dispatch_at IS NOT NULL")


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM optimization_worker_jobs WHERE kind = 'FF') OR
           EXISTS (SELECT 1 FROM optimizaciones WHERE ff_request_key IS NOT NULL) THEN
            RAISE EXCEPTION 'Durable FF history must be retained; use a compatible binary.';
        END IF;
    END $$""")
    op.drop_constraint(DISPATCH, TABLE, type_="check")
    op.drop_column(TABLE, "worker_identity")
    op.drop_column(TABLE, "dispatch_at")
    op.drop_constraint(KIND, JOB, type_="check")
    op.create_check_constraint(KIND, JOB, "kind IN ('V1', 'V2') AND (kind = 'V2' OR state <> 'WAITING')")
    op.drop_constraint("ck_optimizaciones_ff_request", "optimizaciones", type_="check")
    op.drop_constraint("uq_optimizaciones_ff_request_key", "optimizaciones", type_="unique")
    op.drop_column("optimizaciones", "ff_request_key")
