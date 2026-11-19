"""EN-CONC-001: bounded progress and frozen inventory references."""

from alembic import op
import sqlalchemy as sa

revision = "s2conc01b300"
down_revision = "s2conc01b210"
branch_labels = None
depends_on = None

STREAM = "optimization_progress_streams"
EVENT = "optimization_progress_events"
STOCK = "optimization_stock_protections"


def upgrade():
    op.create_table(STREAM,
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("optimization_worker_jobs.comparison_id", ondelete="CASCADE"),
                  primary_key=True, autoincrement=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("active_execution_id", sa.Integer(), sa.ForeignKey("ejecuciones_optimizacion.id_ejecucion")),
        sa.CheckConstraint("last_sequence > 0", name="ck_optimization_progress_sequence"))
    op.create_table(EVENT,
        sa.Column("job_id", sa.Integer(), sa.ForeignKey(f"{STREAM}.job_id", ondelete="CASCADE"),
                  primary_key=True, autoincrement=False),
        sa.Column("sequence", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("execution_id", sa.Integer(), sa.ForeignKey("ejecuciones_optimizacion.id_ejecucion")),
        sa.Column("reason", sa.String(32)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.CheckConstraint("sequence > 0", name="ck_optimization_progress_event_sequence"),
        sa.CheckConstraint("kind IN ('WAITING', 'RUNNING', 'RECOVERY_BLOCKED', 'TERMINAL', 'CANCELLATION_REQUESTED', "
                           "'ENGINE_STARTED', 'ENGINE_FINISHED')", name="ck_optimization_progress_event_kind"),
        sa.CheckConstraint("(kind IN ('ENGINE_STARTED', 'ENGINE_FINISHED')) = (execution_id IS NOT NULL) AND "
                           "(kind = 'ENGINE_FINISHED') = (reason IS NOT NULL)", name="ck_optimization_progress_event_data"))
    op.create_index("ix_optimization_progress_event_expiration", EVENT, ["occurred_at"])
    op.create_table(STOCK,
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("optimization_worker_jobs.comparison_id", ondelete="CASCADE"),
                  primary_key=True, autoincrement=False),
        sa.Column("source", sa.String(7), primary_key=True),
        sa.Column("material_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.CheckConstraint("source IN ('PLANCHA', 'RETAZO') AND material_id > 0 AND units > 0 "
                           "AND (source = 'PLANCHA' OR units = 1)", name="ck_optimization_stock_protection_shape"))
    op.create_index("ix_optimization_stock_protection_material", STOCK, ["source", "material_id"])
    # Existing open jobs keep their frozen references. The migration creates no historical events.
    op.execute("""
        INSERT INTO optimization_stock_protections (job_id, source, material_id, units)
        SELECT j.comparison_id, s->>'source', (s->>'material_id')::integer, max((s->>'physical_unit_index')::integer)
        FROM optimization_worker_jobs j JOIN optimizaciones o ON o.id_optimizacion = j.comparison_id
        LEFT JOIN ejecuciones_optimizacion e ON j.kind = 'FF' AND e.id_optimizacion = j.comparison_id AND e.metodo = 'FF'
        CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN j.kind = 'FF'
            THEN e.patron_resultado #> '{result,problem,groups}'
            ELSE o.comparison_snapshot #> '{input,problem,groups}' END) AS groups(g)
        CROSS JOIN LATERAL jsonb_array_elements(g->'stock_candidates') AS candidates(s)
        WHERE j.state <> 'TERMINAL'
        GROUP BY 1, 2, 3
    """)
    for table in (STREAM, EVENT, STOCK):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM optimization_progress_streams) OR
           EXISTS (SELECT 1 FROM optimization_stock_protections) THEN
            RAISE EXCEPTION 'Keep progress cursors and open stock protections; use a compatible binary.';
        END IF;
    END $$""")
    op.drop_table(STOCK)
    op.drop_table(EVENT)
    op.drop_table(STREAM)
