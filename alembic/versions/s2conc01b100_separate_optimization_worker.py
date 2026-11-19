"""EN-CONC-001 2B.1: durable worker ownership and one global slot."""

from alembic import op
import sqlalchemy as sa

revision = "s2conc01b100"
down_revision = "s2sec01a1b2c"
branch_labels = None
depends_on = None


def upgrade():
    policy = op.create_table(
        "optimization_worker_policy",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("pilot", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.CheckConstraint("id = 1", name="ck_optimization_worker_policy_singleton"),
    )
    op.bulk_insert(policy, [{"id": 1, "pilot": False}])
    op.create_table(
        "optimization_worker_jobs",
        sa.Column("comparison_id", sa.Integer(), sa.ForeignKey("optimizaciones.id_optimizacion"),
                  primary_key=True, autoincrement=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("slot", sa.Integer()),
        sa.Column("owner_token", sa.String(32)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("state IN ('WAITING', 'RUNNING', 'RECOVERY_BLOCKED', 'TERMINAL')",
                           name="ck_optimization_worker_job_state"),
        sa.CheckConstraint("((state IN ('RUNNING', 'RECOVERY_BLOCKED')) AND slot IS NOT NULL AND slot = 1 "
                           "AND owner_token IS NOT NULL AND heartbeat_at IS NOT NULL) OR "
                           "(state IN ('WAITING', 'TERMINAL') AND slot IS NULL)", name="ck_optimization_worker_job_slot"),
        sa.CheckConstraint("(owner_token IS NULL) = (heartbeat_at IS NULL) AND "
                           "(state <> 'WAITING' OR owner_token IS NULL)", name="ck_optimization_worker_job_owner"),
        sa.UniqueConstraint("slot", name="uq_optimization_worker_global_slot"),
    )
    op.create_index("ix_optimization_worker_waiting", "optimization_worker_jobs", ["comparison_id"],
                    postgresql_where=sa.text("state = 'WAITING'"))
    op.execute("ALTER TABLE optimization_worker_policy ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE optimization_worker_jobs ENABLE ROW LEVEL SECURITY")


def downgrade():
    check = sa.text("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM optimization_worker_jobs) OR
           EXISTS (SELECT 1 FROM optimization_worker_policy WHERE pilot) THEN
            RAISE EXCEPTION 'Worker pilot history must be retained. Disable admission using a compatible application.';
        END IF;
    END $$""")
    op.execute(check)
    op.drop_table("optimization_worker_jobs")
    op.drop_table("optimization_worker_policy")
