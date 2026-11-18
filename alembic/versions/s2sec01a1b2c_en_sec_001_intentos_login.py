"""EN-SEC-001: tabla de intentos fallidos de inicio de sesión por cuenta e IP.

Aditiva: crea una tabla nueva y no modifica ninguna existente.
Autor: Fabricio Aguilar. Revisor asignado: Andro Quispe.
Revision ID: s2sec01a1b2c
Revises: s2cmp02d4e6f8
"""
from alembic import op
import sqlalchemy as sa

revision = "s2sec01a1b2c"
down_revision = "s2cmp02d4e6f8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "intentos_login",
        sa.Column("alcance", sa.String(10), nullable=False),
        sa.Column("clave", sa.String(64), nullable=False),
        sa.Column("fallos", sa.Integer(), nullable=False),
        sa.Column("inicio_ventana", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bloqueado_hasta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("alcance", "clave", name="pk_intentos_login"),
        sa.CheckConstraint("alcance IN ('account', 'ip')", name="ck_intentos_login_alcance"),
        sa.CheckConstraint("fallos >= 0", name="ck_intentos_login_fallos"),
    )
    op.create_index("ix_intentos_login_actualizado_en", "intentos_login", ["actualizado_en"])


def downgrade():
    # Solo contiene contadores temporales de seguridad: borrarlos no pierde
    # datos de negocio (en el peor caso, se reinician los bloqueos vigentes).
    op.drop_index("ix_intentos_login_actualizado_en", table_name="intentos_login")
    op.drop_table("intentos_login")
