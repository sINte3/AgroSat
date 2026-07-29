"""Add reproducible tenant-scoped productivity zone runs.

Revision ID: 0010_productivity_zones
Revises: 0009_yield_map_imports
"""

from typing import Sequence, Union

from alembic import op
import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0010_productivity_zones"
down_revision: Union[str, Sequence[str], None] = "0009_yield_map_imports"
branch_labels = None
depends_on = None

RESULT_STATUSES = ("ready", "insufficient_data")
ZONE_CLASSES = ("low", "medium", "high")


def quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "productivity_zone_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("algorithm", sa.String(64), nullable=False),
        sa.Column("algorithm_version", sa.String(32), nullable=False),
        sa.Column("run_key", sa.String(64), nullable=False),
        sa.Column("run_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selected_seasons", postgresql.JSONB(), nullable=False),
        sa.Column("source_import_ids", postgresql.JSONB(), nullable=False),
        sa.Column("source_sha256s", postgresql.JSONB(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("result_status", sa.String(24), nullable=False),
        sa.Column(
            "reason_codes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("point_count", sa.Integer(), nullable=False),
        sa.Column("eligible_cell_count", sa.Integer(), nullable=False),
        sa.Column("field_area_ha", sa.Numeric(14, 4), nullable=True),
        sa.Column("zoned_area_ha", sa.Numeric(14, 4), nullable=True),
        sa.Column("area_delta_ha", sa.Numeric(14, 4), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column(
            "provenance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id"],
            ["enterprises.id"],
            name="fk_productivity_zone_runs_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["field_id", "enterprise_id"],
            ["fields.id", "fields.enterprise_id"],
            name="fk_productivity_zone_runs_field_enterprise",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "enterprise_id",
            "field_id",
            name="uq_productivity_zone_runs_id_enterprise_field",
        ),
        sa.UniqueConstraint(
            "enterprise_id",
            "field_id",
            "run_key",
            name="uq_productivity_zone_runs_key",
        ),
        sa.CheckConstraint(
            "algorithm = 'yield_grid_stability' "
            "AND algorithm_version = 'yield_grid_stability_v1'",
            name="ck_productivity_zone_runs_algorithm",
        ),
        sa.CheckConstraint(
            "run_key ~ '^[0-9a-f]{64}$'",
            name="ck_productivity_zone_runs_key",
        ),
        sa.CheckConstraint(
            f"result_status IN ({quoted(RESULT_STATUSES)})",
            name="ck_productivity_zone_runs_status",
        ),
        sa.CheckConstraint(
            "point_count >= 0 AND eligible_cell_count >= 0",
            name="ck_productivity_zone_runs_counts",
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1",
            name="ck_productivity_zone_runs_confidence",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(selected_seasons) = 'array' "
            "AND jsonb_typeof(source_import_ids) = 'array' "
            "AND jsonb_typeof(source_sha256s) = 'array' "
            "AND jsonb_typeof(parameters) = 'object' "
            "AND jsonb_typeof(reason_codes) = 'array' "
            "AND jsonb_typeof(provenance) = 'object'",
            name="ck_productivity_zone_runs_json_shapes",
        ),
        sa.CheckConstraint(
            "("
            "result_status = 'ready' "
            "AND jsonb_array_length(reason_codes) = 0 "
            "AND point_count >= 60 "
            "AND eligible_cell_count > 0 "
            "AND field_area_ha > 0 "
            "AND zoned_area_ha > 0 "
            "AND zoned_area_ha <= field_area_ha + 0.01 "
            "AND area_delta_ha = field_area_ha - zoned_area_ha "
            "AND finished_at IS NOT NULL"
            ") OR ("
            "result_status = 'insufficient_data' "
            "AND jsonb_array_length(reason_codes) > 0 "
            "AND zoned_area_ha IS NULL "
            "AND area_delta_ha IS NULL "
            "AND finished_at IS NOT NULL"
            ")",
            name="ck_productivity_zone_runs_result_invariants",
        ),
    )
    op.create_index(
        "ix_productivity_zone_runs_enterprise_field_created",
        "productivity_zone_runs",
        ["enterprise_id", "field_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_productivity_zone_runs_enterprise_status",
        "productivity_zone_runs",
        ["enterprise_id", "result_status", sa.text("created_at DESC")],
    )

    op.create_table(
        "productivity_zones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("zone_class", sa.String(16), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry(
                geometry_type="MULTIPOLYGON",
                srid=4326,
                spatial_index=False,
            ),
            nullable=False,
        ),
        sa.Column("area_ha", sa.Numeric(14, 4), nullable=False),
        sa.Column("mean_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column(
            "provenance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "enterprise_id", "field_id"],
            [
                "productivity_zone_runs.id",
                "productivity_zone_runs.enterprise_id",
                "productivity_zone_runs.field_id",
            ],
            name="fk_productivity_zones_run_enterprise_field",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["field_id", "enterprise_id"],
            ["fields.id", "fields.enterprise_id"],
            name="fk_productivity_zones_field_enterprise",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "run_id",
            "zone_class",
            name="uq_productivity_zones_run_class",
        ),
        sa.CheckConstraint(
            f"zone_class IN ({quoted(ZONE_CLASSES)})",
            name="ck_productivity_zones_class",
        ),
        sa.CheckConstraint(
            "area_ha > 0 AND mean_score BETWEEN 0 AND 1 "
            "AND confidence BETWEEN 0 AND 1",
            name="ck_productivity_zones_measures",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(provenance) = 'object'",
            name="ck_productivity_zones_provenance",
        ),
        sa.CheckConstraint(
            "NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry)",
            name="ck_productivity_zones_geometry",
        ),
    )
    op.create_index(
        "ix_productivity_zones_geometry",
        "productivity_zones",
        ["geometry"],
        postgresql_using="gist",
    )
    op.create_index(
        "ix_productivity_zones_enterprise_field",
        "productivity_zones",
        ["enterprise_id", "field_id", "run_id"],
    )


def downgrade() -> None:
    context = op.get_context()
    if not context.as_sql:
        count = op.get_bind().execute(
            sa.text("SELECT count(*) FROM productivity_zone_runs")
        ).scalar_one()
        if count:
            raise RuntimeError(
                "Cannot downgrade while productivity zone runs exist"
            )

    op.drop_index(
        "ix_productivity_zones_enterprise_field",
        table_name="productivity_zones",
    )
    op.drop_index(
        "ix_productivity_zones_geometry",
        table_name="productivity_zones",
    )
    op.drop_table("productivity_zones")
    op.drop_index(
        "ix_productivity_zone_runs_enterprise_status",
        table_name="productivity_zone_runs",
    )
    op.drop_index(
        "ix_productivity_zone_runs_enterprise_field_created",
        table_name="productivity_zone_runs",
    )
    op.drop_table("productivity_zone_runs")
