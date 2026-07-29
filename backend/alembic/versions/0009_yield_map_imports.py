"""Add tenant-scoped point yield map imports.

Revision ID: 0009_yield_map_imports
Revises: 0008_irrigation_context
"""

from typing import Sequence, Union

from alembic import op
import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0009_yield_map_imports"
down_revision: Union[str, Sequence[str], None] = "0008_irrigation_context"
branch_labels = None
depends_on = None

SCHEMA_CODES = ("yield_point_csv_v1",)
INPUT_UNITS = ("t_ha", "kg_ha")


def quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "yield_map_imports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("season_year", sa.Integer(), nullable=False),
        sa.Column("crop_code", sa.String(64), nullable=False),
        sa.Column("schema_code", sa.String(40), nullable=False),
        sa.Column("source_filename", sa.String(255), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("source_provider", sa.String(100), nullable=False),
        sa.Column("machine_id", sa.String(128), nullable=True),
        sa.Column("machine_model", sa.String(128), nullable=True),
        sa.Column("input_unit", sa.String(20), nullable=False),
        sa.Column(
            "normalized_unit",
            sa.String(20),
            nullable=False,
            server_default="t_ha",
        ),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("accepted_rows", sa.Integer(), nullable=False),
        sa.Column("rejected_rows", sa.Integer(), nullable=False),
        sa.Column("yield_min_t_ha", sa.Float(), nullable=False),
        sa.Column("yield_max_t_ha", sa.Float(), nullable=False),
        sa.Column("yield_mean_t_ha", sa.Float(), nullable=False),
        sa.Column(
            "bounds",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "provenance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("client_request_id", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id"],
            ["enterprises.id"],
            name="fk_yield_map_imports_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["field_id", "enterprise_id"],
            ["fields.id", "fields.enterprise_id"],
            name="fk_yield_map_imports_field_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_yield_map_imports_creator",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "enterprise_id",
            "field_id",
            name="uq_yield_map_imports_id_enterprise_field",
        ),
        sa.UniqueConstraint(
            "enterprise_id",
            "field_id",
            "season_year",
            "schema_code",
            "source_sha256",
            name="uq_yield_map_imports_source",
        ),
        sa.UniqueConstraint(
            "enterprise_id",
            "created_by_id",
            "client_request_id",
            name="uq_yield_map_imports_actor_request",
        ),
        sa.CheckConstraint(
            f"schema_code IN ({quoted(SCHEMA_CODES)})",
            name="ck_yield_map_imports_schema_code",
        ),
        sa.CheckConstraint(
            f"input_unit IN ({quoted(INPUT_UNITS)})",
            name="ck_yield_map_imports_input_unit",
        ),
        sa.CheckConstraint(
            "normalized_unit = 't_ha'",
            name="ck_yield_map_imports_normalized_unit",
        ),
        sa.CheckConstraint(
            "season_year BETWEEN 2000 AND 2200",
            name="ck_yield_map_imports_season_year",
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$' "
            "AND request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_yield_map_imports_hashes",
        ),
        sa.CheckConstraint(
            "char_length(source_filename) BETWEEN 1 AND 255 "
            "AND source_filename !~ '[\\\\/]'",
            name="ck_yield_map_imports_safe_filename",
        ),
        sa.CheckConstraint(
            "char_length(crop_code) BETWEEN 2 AND 64 "
            "AND char_length(source_provider) BETWEEN 2 AND 100",
            name="ck_yield_map_imports_metadata",
        ),
        sa.CheckConstraint(
            "status = 'accepted' AND total_rows = accepted_rows "
            "AND rejected_rows = 0 AND total_rows BETWEEN 1 AND 5000",
            name="ck_yield_map_imports_counts_status",
        ),
        sa.CheckConstraint(
            "yield_min_t_ha > 0 "
            "AND yield_max_t_ha >= yield_min_t_ha "
            "AND yield_mean_t_ha BETWEEN yield_min_t_ha AND yield_max_t_ha",
            name="ck_yield_map_imports_summary",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(bounds) = 'object' "
            "AND jsonb_typeof(provenance) = 'object'",
            name="ck_yield_map_imports_json_shapes",
        ),
    )
    op.create_index(
        "ix_yield_map_imports_enterprise_field_season",
        "yield_map_imports",
        [
            "enterprise_id",
            "field_id",
            sa.text("season_year DESC"),
            sa.text("created_at DESC"),
        ],
    )

    op.create_table(
        "yield_map_points",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("import_id", sa.Integer(), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("machine_point_id", sa.String(128), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry(
                geometry_type="POINT",
                srid=4326,
                spatial_index=False,
            ),
            nullable=False,
        ),
        sa.Column("yield_t_ha", sa.Float(), nullable=False),
        sa.Column("speed_kph", sa.Float(), nullable=True),
        sa.Column("moisture_pct", sa.Float(), nullable=True),
        sa.Column(
            "quality_flags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["import_id", "enterprise_id", "field_id"],
            [
                "yield_map_imports.id",
                "yield_map_imports.enterprise_id",
                "yield_map_imports.field_id",
            ],
            name="fk_yield_map_points_import_enterprise_field",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["field_id", "enterprise_id"],
            ["fields.id", "fields.enterprise_id"],
            name="fk_yield_map_points_field_enterprise",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "import_id",
            "source_row",
            name="uq_yield_map_points_import_source_row",
        ),
        sa.CheckConstraint(
            "source_row BETWEEN 1 AND 5000",
            name="ck_yield_map_points_source_row",
        ),
        sa.CheckConstraint(
            "yield_t_ha BETWEEN 0.01 AND 100",
            name="ck_yield_map_points_yield",
        ),
        sa.CheckConstraint(
            "speed_kph IS NULL OR speed_kph BETWEEN 0 AND 200",
            name="ck_yield_map_points_speed",
        ),
        sa.CheckConstraint(
            "moisture_pct IS NULL OR moisture_pct BETWEEN 0 AND 100",
            name="ck_yield_map_points_moisture",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(quality_flags) = 'array'",
            name="ck_yield_map_points_quality_flags",
        ),
        sa.CheckConstraint(
            "NOT ST_IsEmpty(geometry) "
            "AND ST_IsValid(geometry) "
            "AND ST_X(geometry) BETWEEN -180 AND 180 "
            "AND ST_Y(geometry) BETWEEN -90 AND 90",
            name="ck_yield_map_points_geometry",
        ),
    )
    op.create_index(
        "ix_yield_map_points_geometry",
        "yield_map_points",
        ["geometry"],
        postgresql_using="gist",
    )
    op.create_index(
        "ix_yield_map_points_enterprise_field_observed",
        "yield_map_points",
        ["enterprise_id", "field_id", sa.text("observed_at DESC")],
    )
    op.create_index(
        "uq_yield_map_points_import_machine_point",
        "yield_map_points",
        ["import_id", "machine_point_id"],
        unique=True,
        postgresql_where=sa.text("machine_point_id IS NOT NULL"),
    )


def downgrade() -> None:
    context = op.get_context()
    if not context.as_sql:
        count = op.get_bind().execute(
            sa.text("SELECT count(*) FROM yield_map_imports")
        ).scalar_one()
        if count:
            raise RuntimeError(
                "Cannot downgrade while accepted yield map imports exist"
            )

    op.drop_index(
        "uq_yield_map_points_import_machine_point",
        table_name="yield_map_points",
    )
    op.drop_index(
        "ix_yield_map_points_enterprise_field_observed",
        table_name="yield_map_points",
    )
    op.drop_index(
        "ix_yield_map_points_geometry",
        table_name="yield_map_points",
    )
    op.drop_table("yield_map_points")
    op.drop_index(
        "ix_yield_map_imports_enterprise_field_season",
        table_name="yield_map_imports",
    )
    op.drop_table("yield_map_imports")
