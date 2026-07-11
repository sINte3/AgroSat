"""Create the reproducible schema baseline that predates revision 0002."""

from typing import Sequence, Union

from alembic import op
import geoalchemy2
import sqlalchemy as sa


revision: str = "0001_baseline_existing_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the complete pre-0002 application schema."""
    # Later immutable revision identifiers exceed Alembic's default VARCHAR(32).
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "enterprises",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("name_uz", sa.String(255), nullable=True),
        sa.Column("code", sa.String(50), nullable=True),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("contact_person", sa.String(255), nullable=True),
        sa.Column("contact_phone", sa.String(50), nullable=True),
        sa.Column("total_area_ha", sa.Float(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="enterprises_pkey"),
        sa.UniqueConstraint("code", name="enterprises_code_key"),
    )
    op.create_index("ix_enterprises_id", "enterprises", ["id"])

    op.create_table(
        "crop_types",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name_ru", sa.String(100), nullable=False),
        sa.Column("name_uz", sa.String(100), nullable=True),
        sa.Column("name_en", sa.String(100), nullable=True),
        sa.Column("typical_sowing_month", sa.Integer(), nullable=True),
        sa.Column("typical_harvest_month", sa.Integer(), nullable=True),
        sa.Column("growth_stages", sa.JSON(), nullable=True),
        sa.Column("alerts_config", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="crop_types_pkey"),
        sa.UniqueConstraint("code", name="crop_types_code_key"),
    )
    op.create_index("ix_crop_types_id", "crop_types", ["id"])

    op.create_table(
        "fields",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("code", sa.String(50), nullable=True),
        sa.Column("geometry", geoalchemy2.Geometry("POLYGON", srid=4326, spatial_index=False), nullable=False),
        sa.Column("area_ha", sa.Float(), nullable=True),
        sa.Column("centroid_lat", sa.Float(), nullable=True),
        sa.Column("centroid_lon", sa.Float(), nullable=True),
        sa.Column("soil_type", sa.String(100), nullable=True),
        sa.Column("irrigation_type", sa.String(50), nullable=True),
        sa.Column("elevation_m", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], name="fields_enterprise_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="fields_pkey"),
    )
    op.create_index("ix_fields_id", "fields", ["id"])
    op.create_index("idx_fields_enterprise_active", "fields", ["enterprise_id", "is_active"])
    op.create_index("idx_fields_geometry", "fields", ["geometry"], postgresql_using="gist")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("role", sa.String(50), nullable=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_login", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], name="users_enterprise_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="users_pkey"),
        sa.UniqueConstraint("email", name="users_email_key"),
    )
    op.create_index("ix_users_id", "users", ["id"])

    op.create_table(
        "crop_seasons",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("crop_type_id", sa.Integer(), nullable=False),
        sa.Column("season_year", sa.Integer(), nullable=False),
        sa.Column("variety", sa.String(100), nullable=True),
        sa.Column("planting_date", sa.DateTime(), nullable=True),
        sa.Column("expected_harvest_date", sa.DateTime(), nullable=True),
        sa.Column("actual_harvest_date", sa.DateTime(), nullable=True),
        sa.Column("planned_yield_tha", sa.Float(), nullable=True),
        sa.Column("actual_yield_tha", sa.Float(), nullable=True),
        sa.Column("fertilizer_plan", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["crop_type_id"], ["crop_types.id"], name="crop_seasons_crop_type_id_fkey"),
        sa.ForeignKeyConstraint(["field_id"], ["fields.id"], name="crop_seasons_field_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="crop_seasons_pkey"),
    )
    op.create_index("ix_crop_seasons_id", "crop_seasons", ["id"])

    op.create_table(
        "ndvi_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("captured_date", sa.Date(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("mean_ndvi", sa.Float(), nullable=True),
        sa.Column("min_ndvi", sa.Float(), nullable=True),
        sa.Column("max_ndvi", sa.Float(), nullable=True),
        sa.Column("std_ndvi", sa.Float(), nullable=True),
        sa.Column("p10_ndvi", sa.Float(), nullable=True),
        sa.Column("p90_ndvi", sa.Float(), nullable=True),
        sa.Column("cloud_cover_pct", sa.Float(), nullable=True),
        sa.Column("valid_pixels_pct", sa.Float(), nullable=True),
        sa.Column("satellite", sa.String(50), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("ndvi_change", sa.Float(), nullable=True),
        sa.Column("ndvi_change_pct", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["field_id"], ["fields.id"], name="ndvi_records_field_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="ndvi_records_pkey"),
    )
    op.create_index("ix_ndvi_records_id", "ndvi_records", ["id"])
    op.create_index("idx_ndvi_records_field_date_desc", "ndvi_records", ["field_id", sa.text("captured_date DESC")])

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("ndvi_record_id", sa.Integer(), nullable=True),
        sa.Column("alert_type", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("triggered_value", sa.Float(), nullable=True),
        sa.Column("threshold_value", sa.Float(), nullable=True),
        sa.Column("triggered_at", sa.DateTime(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("acknowledged_by_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(["acknowledged_by_id"], ["users.id"], name="alerts_acknowledged_by_id_fkey"),
        sa.ForeignKeyConstraint(["field_id"], ["fields.id"], name="alerts_field_id_fkey"),
        sa.ForeignKeyConstraint(["ndvi_record_id"], ["ndvi_records.id"], name="alerts_ndvi_record_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="alerts_pkey"),
    )
    op.create_index("ix_alerts_id", "alerts", ["id"])
    op.create_index("idx_alerts_field_active_severity", "alerts", ["field_id", "is_active", "severity"])

    op.create_table(
        "scouting_notes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("problem_type", sa.String(100), nullable=True),
        sa.Column("severity_estimate", sa.String(20), nullable=True),
        sa.Column("point_lat", sa.Float(), nullable=True),
        sa.Column("point_lon", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], name="scouting_notes_author_id_fkey"),
        sa.ForeignKeyConstraint(["field_id"], ["fields.id"], name="scouting_notes_field_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="scouting_notes_pkey"),
    )
    op.create_index("ix_scouting_notes_id", "scouting_notes", ["id"])


def downgrade() -> None:
    """Remove baseline application tables in dependency-safe order."""
    op.drop_table("scouting_notes")
    op.drop_table("alerts")
    op.drop_table("ndvi_records")
    op.drop_table("crop_seasons")
    op.drop_table("users")
    op.drop_table("fields")
    op.drop_table("crop_types")
    op.drop_table("enterprises")
