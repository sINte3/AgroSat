"""create satellite_index_records table

Revision ID: 0002_create_satellite_index_records
Revises: 0001_baseline_existing_schema
Create Date: 2026-06-26 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import geoalchemy2


# revision identifiers, used by Alembic.
revision: str = '0002_create_satellite_index_records'
down_revision: Union[str, Sequence[str], None] = '0001_baseline_existing_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create satellite_index_records table."""
    op.create_table(
        "satellite_index_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("captured_date", sa.Date(), nullable=False),
        sa.Column("index_code", sa.String(length=20), nullable=False),
        sa.Column("mean_value", sa.Float(), nullable=True),
        sa.Column("min_value", sa.Float(), nullable=True),
        sa.Column("max_value", sa.Float(), nullable=True),
        sa.Column("std_value", sa.Float(), nullable=True),
        sa.Column("p10_value", sa.Float(), nullable=True),
        sa.Column("p90_value", sa.Float(), nullable=True),
        sa.Column("valid_pixels_pct", sa.Float(), nullable=True),
        sa.Column("cloud_cover_pct", sa.Float(), nullable=True),
        sa.Column("satellite", sa.String(length=50), nullable=True,
                  server_default="Sentinel-2"),
        sa.Column("created_at", sa.DateTime(), nullable=True,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["field_id"], ["fields.id"],
            name="fk_satellite_index_records_field_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_satellite_index_records"),
        sa.UniqueConstraint(
            "field_id", "captured_date", "index_code",
            name="uq_satellite_index_records_field_date_code",
        ),
    )
    op.create_index(
        "ix_satellite_index_records_field_code_date",
        "satellite_index_records",
        ["field_id", "index_code", "captured_date"],
    )
    op.create_index(
        "ix_satellite_index_records_captured_date",
        "satellite_index_records",
        ["captured_date"],
    )
    op.create_index(
        "ix_satellite_index_records_index_code",
        "satellite_index_records",
        ["index_code"],
    )


def downgrade() -> None:
    """Drop satellite_index_records table."""
    op.drop_index("ix_satellite_index_records_index_code",
                  table_name="satellite_index_records")
    op.drop_index("ix_satellite_index_records_captured_date",
                  table_name="satellite_index_records")
    op.drop_index("ix_satellite_index_records_field_code_date",
                  table_name="satellite_index_records")
    op.drop_table("satellite_index_records")
