"""Create the field inspection assignment and lifecycle table."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005_field_inspections"
down_revision: Union[str, Sequence[str], None] = "0004_repair_core_constraints"
branch_labels = None
depends_on = None

ACTIVE_PREDICATE = "status IN ('pending','in_progress')"


def upgrade() -> None:
    op.create_table(
        "field_inspections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("field_id", sa.Integer(), sa.ForeignKey("fields.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), sa.ForeignKey("enterprises.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("assigned_to_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("client_request_id", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("source", sa.String(30), nullable=False, server_default="attention_queue"),
        sa.Column("source_priority", sa.String(20), nullable=True),
        sa.Column("source_attention_score", sa.SmallInteger(), nullable=True),
        sa.Column("source_observation_date", sa.Date(), nullable=True),
        sa.Column("source_reason_codes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("completion_summary", sa.Text(), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending','in_progress','completed','cancelled')", name="ck_field_inspections_status"),
        sa.CheckConstraint("source IN ('attention_queue','manual')", name="ck_field_inspections_source"),
        sa.CheckConstraint("source_priority IS NULL OR source_priority IN ('low','medium','high','critical')", name="ck_field_inspections_source_priority"),
        sa.CheckConstraint("source_attention_score IS NULL OR source_attention_score BETWEEN 0 AND 100", name="ck_field_inspections_attention_score"),
        sa.CheckConstraint("version >= 1", name="ck_field_inspections_version"),
        sa.CheckConstraint("(status = 'completed' AND completed_at IS NOT NULL AND cancelled_at IS NULL) OR (status = 'cancelled' AND cancelled_at IS NOT NULL AND completed_at IS NULL) OR (status IN ('pending','in_progress') AND completed_at IS NULL AND cancelled_at IS NULL)", name="ck_field_inspections_terminal_timestamps"),
    )
    op.create_index("uq_field_inspections_client_request_id", "field_inspections", ["client_request_id"], unique=True)
    op.create_index("uq_field_inspections_one_active_per_field", "field_inspections", ["field_id"], unique=True, postgresql_where=sa.text(ACTIVE_PREDICATE))
    op.create_index("ix_field_inspections_enterprise_status_due", "field_inspections", ["enterprise_id", "status", "due_date"])
    op.create_index("ix_field_inspections_assigned_status_due", "field_inspections", ["assigned_to_id", "status", "due_date"])
    op.create_index("ix_field_inspections_field_created", "field_inspections", ["field_id", sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("ix_field_inspections_field_created", table_name="field_inspections")
    op.drop_index("ix_field_inspections_assigned_status_due", table_name="field_inspections")
    op.drop_index("ix_field_inspections_enterprise_status_due", table_name="field_inspections")
    op.drop_index("uq_field_inspections_one_active_per_field", table_name="field_inspections")
    op.drop_index("uq_field_inspections_client_request_id", table_name="field_inspections")
    op.drop_table("field_inspections")
