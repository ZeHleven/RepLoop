"""Per-account lifetime model budget and per-HTTP-attempt ledger."""
from alembic import op
import sqlalchemy as sa

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("model_budgets",
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("spent_nano", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved_nano", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("spent_nano >= 0 AND reserved_nano >= 0", name="ck_model_budget_nonnegative"))
    op.create_table("model_charges",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("model_budgets.user_id"), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("pricing_version", sa.String(80), nullable=False),
        sa.Column("reserved_nano", sa.BigInteger(), nullable=False),
        sa.Column("actual_nano", sa.BigInteger(), nullable=True),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=True),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_model_charges_user_id", "model_charges", ["user_id"])


def downgrade():
    # Losing the ledger would silently renew everybody's allowance.
    raise RuntimeError("Budget ledger downgrade is disabled; retain these tables on application rollback.")
