"""EN-CONC-001 2B.2: generation, attempt evidence and mixed admission."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "s2conc01b200"
down_revision = "s2conc01b100"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("optimization_worker_policy", sa.Column("mixed", sa.Boolean(), nullable=False,
                                                         server_default=sa.text("false")))
    op.create_check_constraint("ck_optimization_worker_policy_mixed", "optimization_worker_policy", "NOT mixed OR pilot")
    op.add_column("optimization_worker_jobs", sa.Column("kind", sa.String(2), nullable=False, server_default="V2"))
    op.add_column("optimization_worker_jobs", sa.Column("generation", sa.BigInteger(), nullable=False, server_default="0"))
    op.execute("UPDATE optimization_worker_jobs SET generation = 1 WHERE owner_token IS NOT NULL")
    op.create_check_constraint("ck_optimization_worker_job_generation", "optimization_worker_jobs",
                               "generation >= 0 AND (owner_token IS NULL OR generation > 0)")
    op.create_check_constraint("ck_optimization_worker_job_kind", "optimization_worker_jobs",
                               "kind IN ('V1', 'V2') AND (kind = 'V2' OR state <> 'WAITING')")
    op.create_table("optimization_worker_attempts",
        sa.Column("comparison_id", sa.Integer(), sa.ForeignKey("optimization_worker_jobs.comparison_id"),
                  primary_key=True, autoincrement=False),
        sa.Column("generation", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("owner_token", sa.String(32), nullable=False),
        sa.Column("owner_identity", JSONB(none_as_null=True)),
        sa.Column("owner_scope", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closure", sa.String(32)),
        sa.CheckConstraint("generation > 0", name="ck_optimization_worker_attempt_generation"),
        sa.CheckConstraint("(closed_at IS NULL) = (closure IS NULL)", name="ck_optimization_worker_attempt_closure"),
        sa.UniqueConstraint("owner_token", name="uq_optimization_worker_attempt_owner"))
    # The historical schema recorded only the latest heartbeat, not claim time.
    op.execute("""
        INSERT INTO optimization_worker_attempts (comparison_id, generation, owner_token, owner_identity, owner_scope,
                                                   started_at, closed_at, closure)
        SELECT j.comparison_id, j.generation, j.owner_token,
               NULLIF(o.comparison_snapshot #> '{runtime,supervisor,identity}', 'null'::jsonb),
               o.comparison_snapshot #>> '{runtime,owner_scope}', j.heartbeat_at,
               CASE WHEN j.state = 'TERMINAL' THEN COALESCE(o.fecha_fin, j.heartbeat_at) END,
               CASE WHEN j.state = 'TERMINAL' THEN 'IMPORTED' END
        FROM optimization_worker_jobs j JOIN optimizaciones o ON o.id_optimizacion = j.comparison_id
        WHERE j.owner_token IS NOT NULL
    """)
    op.execute("ALTER TABLE optimization_worker_attempts ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM optimization_worker_attempts) OR
           EXISTS (SELECT 1 FROM optimization_worker_policy WHERE mixed) THEN
            RAISE EXCEPTION 'Fencing history must be retained; use a compatible binary.';
        END IF;
    END $$""")
    op.drop_table("optimization_worker_attempts")
    op.drop_constraint("ck_optimization_worker_job_kind", "optimization_worker_jobs", type_="check")
    op.drop_constraint("ck_optimization_worker_job_generation", "optimization_worker_jobs", type_="check")
    op.drop_column("optimization_worker_jobs", "generation")
    op.drop_column("optimization_worker_jobs", "kind")
    op.drop_constraint("ck_optimization_worker_policy_mixed", "optimization_worker_policy", type_="check")
    op.drop_column("optimization_worker_policy", "mixed")
