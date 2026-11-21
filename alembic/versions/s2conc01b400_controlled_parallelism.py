"""EN-CONC-001: two stable slot identities with opt-in Linux resource attestation."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "s2conc01b400"
down_revision = "s2conc01b300"
branch_labels = None
depends_on = None

JOB = "optimization_worker_jobs"
SLOT = "optimization_worker_slots"
CHECK = "ck_optimization_worker_job_slot"
FK = "fk_optimization_worker_enabled_slot"
EXCLUSION = "ex_optimization_worker_exclusive_legacy"
ACTIVE = "state IN ('RUNNING', 'RECOVERY_BLOCKED') AND slot IS NOT NULL"
OWNER = "AND owner_token IS NOT NULL AND heartbeat_at IS NOT NULL"
INACTIVE = "OR (state IN ('WAITING', 'TERMINAL') AND slot IS NULL)"


def upgrade():
    op.create_table(SLOT, sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("lab", JSONB(none_as_null=True)), sa.CheckConstraint(
            "(id = 1 AND lab IS NULL) OR (id = 2 AND lab IS NOT NULL AND jsonb_typeof(lab) = 'object')",
            name="ck_optimization_worker_slot_config"))
    op.execute(f"INSERT INTO {SLOT} (id) VALUES (1)")
    op.execute(f"ALTER TABLE {SLOT} ENABLE ROW LEVEL SECURITY")
    op.create_foreign_key(FK, JOB, SLOT, ["slot"], ["id"])
    op.drop_constraint(CHECK, JOB, type_="check")
    op.create_check_constraint(CHECK, JOB,
        f"({ACTIVE} AND slot IN (1, 2) AND (slot = 1 OR kind = 'V2') {OWNER}) {INACTIVE}")
    # Legacy claims cover both slot identities. The existing UNIQUE still prevents duplicate slot IDs.
    op.execute(f"""ALTER TABLE {JOB} ADD CONSTRAINT {EXCLUSION} EXCLUDE USING gist
        ((CASE WHEN slot IS NULL THEN NULL ELSE int4range(slot,
            CASE WHEN kind = 'V2' THEN slot + 1 ELSE 3 END) END) WITH &&)""")


def downgrade():
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM {SLOT} WHERE id = 2) THEN
            RAISE EXCEPTION 'Disable laboratory capacity after draining before downgrading';
        END IF;
    END $$""")
    op.execute(f"ALTER TABLE {JOB} DROP CONSTRAINT {EXCLUSION}")
    op.drop_constraint(CHECK, JOB, type_="check")
    op.create_check_constraint(CHECK, JOB, f"({ACTIVE} AND slot = 1 {OWNER}) {INACTIVE}")
    op.drop_constraint(FK, JOB, type_="foreignkey")
    op.drop_table(SLOT)
