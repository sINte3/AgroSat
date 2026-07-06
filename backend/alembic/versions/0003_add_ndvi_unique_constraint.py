"""add unique constraint on ndvi_records(field_id, captured_date)

Revision ID: 0003_add_ndvi_unique_constraint
Revises: 0002_create_satellite_index_records
Create Date: 2026-07-06 13:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003_add_ndvi_unique_constraint'
down_revision: Union[str, Sequence[str], None] = '0002_create_satellite_index_records'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add unique constraint on ndvi_records(field_id, captured_date)."""
    op.create_unique_constraint(
        "uq_ndvi_records_field_captured_date",
        "ndvi_records",
        ["field_id", "captured_date"],
    )


def downgrade() -> None:
    """Drop unique constraint on ndvi_records(field_id, captured_date)."""
    op.drop_constraint(
        "uq_ndvi_records_field_captured_date",
        "ndvi_records",
        type_="unique",
    )
