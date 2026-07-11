"""Repair core uniqueness constraints.

Revision ID: 0004_repair_core_constraints
Revises: 478de3d1f6d0
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_repair_core_constraints"
down_revision: Union[str, Sequence[str], None] = "478de3d1f6d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACTIVE_SOURCE_PREDICATE = (
    "is_active = true AND source IS NOT NULL AND source_key IS NOT NULL"
)


def _duplicate_group_count(sql: str) -> int:
    return int(op.get_bind().execute(sa.text(sql)).scalar_one())


def upgrade() -> None:
    """Precheck existing data, then enforce the two core invariants."""
    crop_duplicates = _duplicate_group_count(
        "SELECT count(*) FROM ("
        "SELECT field_id, season_year FROM crop_seasons "
        "GROUP BY field_id, season_year HAVING count(*) > 1"
        ") AS duplicate_groups"
    )
    if crop_duplicates:
        raise RuntimeError(
            "Cannot enforce crop-season uniqueness: duplicate groups exist."
        )

    alert_duplicates = _duplicate_group_count(
        "SELECT count(*) FROM ("
        "SELECT source, source_key FROM alerts "
        "WHERE is_active = true AND source IS NOT NULL AND source_key IS NOT NULL "
        "GROUP BY source, source_key HAVING count(*) > 1"
        ") AS duplicate_groups"
    )
    if alert_duplicates:
        raise RuntimeError(
            "Cannot enforce active-alert source uniqueness: duplicate groups exist."
        )

    op.drop_index("uq_alerts_active_source_source_key", table_name="alerts")
    op.create_index(
        "uq_alerts_active_source_source_key",
        "alerts",
        ["source", "source_key"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_SOURCE_PREDICATE),
    )
    op.create_unique_constraint(
        "uq_crop_seasons_field_season_year",
        "crop_seasons",
        ["field_id", "season_year"],
    )


def downgrade() -> None:
    """Restore the exact constraint state owned by the preceding revision."""
    op.drop_constraint(
        "uq_crop_seasons_field_season_year", "crop_seasons", type_="unique"
    )
    op.drop_index("uq_alerts_active_source_source_key", table_name="alerts")
    op.create_index(
        "uq_alerts_active_source_source_key",
        "alerts",
        ["source", "source_key"],
        postgresql_where=sa.text(ACTIVE_SOURCE_PREDICATE),
    )
