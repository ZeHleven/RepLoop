"""Isolated demo visits and reset identities, with a persistent billing owner."""
from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("demo_visits",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("client_hash", sa.String(64), nullable=False),
        sa.Column("budget_user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("current_user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reset_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_demo_visits_client_hash", "demo_visits", ["client_hash"])
    op.create_table("demo_identities",
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("visit_id", sa.String(), sa.ForeignKey("demo_visits.id"), nullable=False))
    op.create_index("ix_demo_identities_visit_id", "demo_identities", ["visit_id"])


def downgrade():
    # Rollback must not renew anonymous visitors' expired or consumed allowances.
    raise RuntimeError("Retain demo identity and billing history on application rollback.")
