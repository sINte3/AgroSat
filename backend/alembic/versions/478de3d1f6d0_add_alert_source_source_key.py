"""add alert source and source_key for idempotent satellite alerts

Revision ID: 478de3d1f6d0
Revises: 0003_add_ndvi_unique_constraint
Create Date: 2026-07-06 16:26:42.810350

Adds nullable source/source_key columns and two indexes:
- ix_alerts_source_source_key (non-unique, normal lookup)
- uq_alerts_active_source_source_key (partial unique for active alerts only)
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '478de3d1f6d0'
down_revision: Union[str, Sequence[str], None] = '0003_add_ndvi_unique_constraint'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add source, source_key and indexes for idempotent alert persistence."""
    # Add nullable source column
    op.add_column(
        "alerts",
        sa.Column("source", sa.String(64), nullable=True),
    )
    # Add nullable source_key column
    op.add_column(
        "alerts",
        sa.Column("source_key", sa.String(64), nullable=True),
    )
    # Normal non-unique index for lookup by source metadata
    op.create_index(
        "ix_alerts_source_source_key",
        "alerts",
        ["source", "source_key"],
    )
    # Partial unique index: active alerts only, source and source_key must be non-null
    op.create_index(
        "uq_alerts_active_source_source_key",
        "alerts",
        ["source", "source_key"],
        postgresql_where=sa.text(
            "is_active = true AND source IS NOT NULL AND source_key IS NOT NULL"
        ),
    )


def downgrade() -> None:
    """Reverse: drop indexes and columns."""
    op.drop_index("uq_alerts_active_source_source_key", table_name="alerts")
    op.drop_index("ix_alerts_source_source_key", table_name="alerts")
    op.drop_column("alerts", "source_key")
    op.drop_column("alerts", "source")
