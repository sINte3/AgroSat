"""Add tenant-scoped pixel anomaly runs, zones, and inspection links.

Revision ID: 0007_pixel_anomalies
Revises: 0006_operational_closure
"""

from typing import Sequence, Union

from alembic import op
import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0007_pixel_anomalies"
down_revision: Union[str, Sequence[str], None] = "0006_operational_closure"
branch_labels = None
depends_on = None

INDEX_CODES = ("ndvi", "savi", "evi", "ndmi", "ndre")
RUN_STATUSES = ("detected", "no_anomaly", "insufficient_data")
CLASSIFICATIONS = ("single_scene", "persistent", "recovering")
ANOMALY_STATUSES = ("open", "inspection_created", "dismissed", "resolved")


def quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # Composite reference targets make field and tenant ownership enforceable
    # by PostgreSQL instead of relying only on application predicates.
    op.create_unique_constraint(
        "uq_fields_id_enterprise_id",
        "fields",
        ["id", "enterprise_id"],
    )
    op.create_unique_constraint(
        "uq_ndvi_records_id_field_id",
        "ndvi_records",
        ["id", "field_id"],
    )
    op.create_unique_constraint(
        "uq_satellite_index_records_id_field_index",
        "satellite_index_records",
        ["id", "field_id", "index_code"],
    )

    op.create_table(
        "pixel_anomaly_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("index_code", sa.String(20), nullable=False),
        sa.Column("current_record_type", sa.String(30), nullable=False),
        sa.Column("current_ndvi_record_id", sa.Integer(), nullable=True),
        sa.Column(
            "current_satellite_index_record_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column("current_observation_date", sa.Date(), nullable=False),
        sa.Column("comparison_record_type", sa.String(30), nullable=True),
        sa.Column("comparison_ndvi_record_id", sa.Integer(), nullable=True),
        sa.Column(
            "comparison_satellite_index_record_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column("comparison_observation_date", sa.Date(), nullable=True),
        sa.Column("algorithm_version", sa.String(64), nullable=False),
        sa.Column("run_key", sa.String(64), nullable=False),
        sa.Column("threshold_hash", sa.String(64), nullable=False),
        sa.Column(
            "thresholds",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "quality_summary",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("result_status", sa.String(30), nullable=False),
        sa.Column(
            "reason_codes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
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
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["field_id", "enterprise_id"],
            ["fields.id", "fields.enterprise_id"],
            name="fk_pixel_anomaly_runs_field_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id"],
            ["enterprises.id"],
            name="fk_pixel_anomaly_runs_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["current_ndvi_record_id", "field_id"],
            ["ndvi_records.id", "ndvi_records.field_id"],
            name="fk_pixel_anomaly_runs_current_ndvi_field",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "current_satellite_index_record_id",
                "field_id",
                "index_code",
            ],
            [
                "satellite_index_records.id",
                "satellite_index_records.field_id",
                "satellite_index_records.index_code",
            ],
            name="fk_pixel_anomaly_runs_current_satellite_field",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["comparison_ndvi_record_id", "field_id"],
            ["ndvi_records.id", "ndvi_records.field_id"],
            name="fk_pixel_anomaly_runs_comparison_ndvi_field",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "comparison_satellite_index_record_id",
                "field_id",
                "index_code",
            ],
            [
                "satellite_index_records.id",
                "satellite_index_records.field_id",
                "satellite_index_records.index_code",
            ],
            name="fk_pixel_anomaly_runs_comparison_satellite_field",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "enterprise_id",
            name="uq_pixel_anomaly_runs_id_enterprise_id",
        ),
        sa.UniqueConstraint("run_key", name="uq_pixel_anomaly_runs_run_key"),
        sa.CheckConstraint(
            f"index_code IN ({quoted(INDEX_CODES)})",
            name="ck_pixel_anomaly_runs_index_code",
        ),
        sa.CheckConstraint(
            "(current_record_type = 'ndvi_record' "
            "AND index_code = 'ndvi' "
            "AND current_ndvi_record_id IS NOT NULL "
            "AND current_satellite_index_record_id IS NULL) OR "
            "(current_record_type = 'satellite_index_record' "
            "AND current_ndvi_record_id IS NULL "
            "AND current_satellite_index_record_id IS NOT NULL)",
            name="ck_pixel_anomaly_runs_current_record",
        ),
        sa.CheckConstraint(
            "(comparison_record_type IS NULL "
            "AND comparison_ndvi_record_id IS NULL "
            "AND comparison_satellite_index_record_id IS NULL "
            "AND comparison_observation_date IS NULL) OR "
            "(comparison_record_type = 'ndvi_record' "
            "AND index_code = 'ndvi' "
            "AND comparison_ndvi_record_id IS NOT NULL "
            "AND comparison_satellite_index_record_id IS NULL "
            "AND comparison_observation_date IS NOT NULL) OR "
            "(comparison_record_type = 'satellite_index_record' "
            "AND comparison_ndvi_record_id IS NULL "
            "AND comparison_satellite_index_record_id IS NOT NULL "
            "AND comparison_observation_date IS NOT NULL)",
            name="ck_pixel_anomaly_runs_comparison_record",
        ),
        sa.CheckConstraint(
            "result_status = 'insufficient_data' "
            "OR comparison_record_type IS NOT NULL",
            name="ck_pixel_anomaly_runs_success_has_comparison",
        ),
        sa.CheckConstraint(
            "comparison_observation_date IS NULL OR "
            "current_observation_date - comparison_observation_date "
            "BETWEEN 1 AND 365",
            name="ck_pixel_anomaly_runs_temporal_order",
        ),
        sa.CheckConstraint(
            "run_key ~ '^[0-9a-f]{64}$'",
            name="ck_pixel_anomaly_runs_run_key",
        ),
        sa.CheckConstraint(
            "threshold_hash ~ '^[0-9a-f]{64}$'",
            name="ck_pixel_anomaly_runs_threshold_hash",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(thresholds) = 'object' "
            "AND jsonb_typeof(quality_summary) = 'object' "
            "AND jsonb_typeof(reason_codes) = 'array' "
            "AND jsonb_typeof(provenance) = 'object'",
            name="ck_pixel_anomaly_runs_json_shapes",
        ),
        sa.CheckConstraint(
            f"result_status IN ({quoted(RUN_STATUSES)})",
            name="ck_pixel_anomaly_runs_result_status",
        ),
        sa.CheckConstraint(
            "(result_status = 'insufficient_data' "
            "AND jsonb_array_length(reason_codes) > 0) OR "
            "(result_status <> 'insufficient_data' "
            "AND jsonb_array_length(reason_codes) = 0)",
            name="ck_pixel_anomaly_runs_reason_codes",
        ),
        sa.CheckConstraint(
            "finished_at >= started_at",
            name="ck_pixel_anomaly_runs_finished_at",
        ),
    )
    op.create_index(
        "ix_pixel_anomaly_runs_enterprise_field_current",
        "pixel_anomaly_runs",
        ["enterprise_id", "field_id", sa.text("current_observation_date DESC")],
    )
    op.create_index(
        "ix_pixel_anomaly_runs_enterprise_status_created",
        "pixel_anomaly_runs",
        ["enterprise_id", "result_status", sa.text("created_at DESC")],
    )

    op.create_table(
        "pixel_anomalies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("index_code", sa.String(20), nullable=False),
        sa.Column("algorithm_version", sa.String(64), nullable=False),
        sa.Column("zone_key", sa.String(64), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry(
                geometry_type="MULTIPOLYGON",
                srid=4326,
                spatial_index=False,
            ),
            nullable=False,
        ),
        sa.Column("area_ha", sa.Float(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("persistence_count", sa.Integer(), nullable=False),
        sa.Column("classification", sa.String(30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "status",
            sa.String(30),
            nullable=False,
            server_default="open",
        ),
        sa.Column(
            "quality_summary",
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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "enterprise_id"],
            ["pixel_anomaly_runs.id", "pixel_anomaly_runs.enterprise_id"],
            name="fk_pixel_anomalies_run_enterprise",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["field_id", "enterprise_id"],
            ["fields.id", "fields.enterprise_id"],
            name="fk_pixel_anomalies_field_enterprise",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "enterprise_id",
            name="uq_pixel_anomalies_id_enterprise_id",
        ),
        sa.UniqueConstraint(
            "run_id",
            "zone_key",
            name="uq_pixel_anomalies_run_zone_key",
        ),
        sa.CheckConstraint(
            f"index_code IN ({quoted(INDEX_CODES)})",
            name="ck_pixel_anomalies_index_code",
        ),
        sa.CheckConstraint(
            "zone_key ~ '^[0-9a-f]{64}$'",
            name="ck_pixel_anomalies_zone_key",
        ),
        sa.CheckConstraint(
            "area_ha > 0",
            name="ck_pixel_anomalies_area",
        ),
        sa.CheckConstraint(
            "score BETWEEN 0 AND 1 AND confidence BETWEEN 0 AND 1",
            name="ck_pixel_anomalies_score_confidence",
        ),
        sa.CheckConstraint(
            "severity IN ('low','medium','high','critical')",
            name="ck_pixel_anomalies_severity",
        ),
        sa.CheckConstraint(
            "persistence_count >= 1",
            name="ck_pixel_anomalies_persistence_count",
        ),
        sa.CheckConstraint(
            f"classification IN ({quoted(CLASSIFICATIONS)})",
            name="ck_pixel_anomalies_classification",
        ),
        sa.CheckConstraint(
            f"status IN ({quoted(ANOMALY_STATUSES)})",
            name="ck_pixel_anomalies_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(quality_summary) = 'object' "
            "AND jsonb_typeof(provenance) = 'object'",
            name="ck_pixel_anomalies_json_shapes",
        ),
        sa.CheckConstraint(
            "NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry)",
            name="ck_pixel_anomalies_geometry_valid",
        ),
    )
    op.create_index(
        "ix_pixel_anomalies_geometry",
        "pixel_anomalies",
        ["geometry"],
        postgresql_using="gist",
    )
    op.create_index(
        "ix_pixel_anomalies_enterprise_field_status",
        "pixel_anomalies",
        ["enterprise_id", "field_id", "status"],
    )
    op.create_index(
        "ix_pixel_anomalies_enterprise_classification_created",
        "pixel_anomalies",
        ["enterprise_id", "classification", sa.text("created_at DESC")],
    )

    op.create_table(
        "pixel_anomaly_inspections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("anomaly_id", sa.Integer(), nullable=False),
        sa.Column("inspection_id", sa.Integer(), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["anomaly_id", "enterprise_id"],
            ["pixel_anomalies.id", "pixel_anomalies.enterprise_id"],
            name="fk_pixel_anomaly_inspections_anomaly_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inspection_id", "enterprise_id"],
            ["field_inspections.id", "field_inspections.enterprise_id"],
            name="fk_pixel_anomaly_inspections_inspection_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["field_id", "enterprise_id"],
            ["fields.id", "fields.enterprise_id"],
            name="fk_pixel_anomaly_inspections_field_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_pixel_anomaly_inspections_created_by",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "anomaly_id",
            name="uq_pixel_anomaly_inspections_anomaly_id",
        ),
        sa.UniqueConstraint(
            "inspection_id",
            name="uq_pixel_anomaly_inspections_inspection_id",
        ),
        sa.UniqueConstraint(
            "created_by_id",
            "idempotency_key",
            name="uq_pixel_anomaly_inspections_actor_idempotency",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_pixel_anomaly_inspections_request_fingerprint",
        ),
    )
    op.create_index(
        "ix_pixel_anomaly_inspections_enterprise_field",
        "pixel_anomaly_inspections",
        ["enterprise_id", "field_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pixel_anomaly_inspections_enterprise_field",
        table_name="pixel_anomaly_inspections",
    )
    op.drop_table("pixel_anomaly_inspections")

    op.drop_index(
        "ix_pixel_anomalies_enterprise_classification_created",
        table_name="pixel_anomalies",
    )
    op.drop_index(
        "ix_pixel_anomalies_enterprise_field_status",
        table_name="pixel_anomalies",
    )
    op.drop_index(
        "ix_pixel_anomalies_geometry",
        table_name="pixel_anomalies",
    )
    op.drop_table("pixel_anomalies")

    op.drop_index(
        "ix_pixel_anomaly_runs_enterprise_status_created",
        table_name="pixel_anomaly_runs",
    )
    op.drop_index(
        "ix_pixel_anomaly_runs_enterprise_field_current",
        table_name="pixel_anomaly_runs",
    )
    op.drop_table("pixel_anomaly_runs")

    op.drop_constraint(
        "uq_satellite_index_records_id_field_index",
        "satellite_index_records",
        type_="unique",
    )
    op.drop_constraint(
        "uq_ndvi_records_id_field_id",
        "ndvi_records",
        type_="unique",
    )
    op.drop_constraint(
        "uq_fields_id_enterprise_id",
        "fields",
        type_="unique",
    )
