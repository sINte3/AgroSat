"""Add operational closure, evidence, audit, and verification entities.

Revision ID: 0006_operational_closure
Revises: 0005_field_inspections
"""

from typing import Sequence, Union

from alembic import op
import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0006_operational_closure"
down_revision: Union[str, Sequence[str], None] = "0005_field_inspections"
branch_labels = None
depends_on = None

ALEMBIC_VERSION_LENGTH = 64
LEGACY_ALEMBIC_VERSION_LENGTH = 32

CAUSE_CODES = (
    "irrigation",
    "pest",
    "disease",
    "nutrient",
    "weather",
    "soil",
    "mechanical",
    "crop_stage",
    "no_issue",
    "other",
    "unconfirmed",
)
ACTION_STATUSES = ("open", "in_progress", "blocked", "closed")
INDEX_CODES = ("ndvi", "savi", "evi", "ndmi", "ndre")
VERIFICATION_RESULTS = (
    "improved",
    "unchanged",
    "worsened",
    "insufficient_data",
)
AUDIT_EVENTS = (
    "inspection_result_recorded",
    "evidence_attached",
    "action_created",
    "action_updated",
    "action_closed",
    "action_reopened",
    "verification_requested",
    "verification_resolved",
)


def quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _expand_alembic_version_capacity() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=LEGACY_ALEMBIC_VERSION_LENGTH),
        type_=sa.String(length=ALEMBIC_VERSION_LENGTH),
        existing_nullable=False,
    )


def _restore_alembic_version_capacity() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=ALEMBIC_VERSION_LENGTH),
        type_=sa.String(length=LEGACY_ALEMBIC_VERSION_LENGTH),
        existing_nullable=False,
    )


def upgrade() -> None:
    _expand_alembic_version_capacity()

    op.create_unique_constraint(
        "uq_field_inspections_id_enterprise_id",
        "field_inspections",
        ["id", "enterprise_id"],
    )

    op.create_table(
        "inspection_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("inspection_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("recorded_by_id", sa.Integer(), nullable=False),
        sa.Column("cause_code", sa.String(30), nullable=False),
        sa.Column("cause_details", sa.Text(), nullable=True),
        sa.Column("evidence_note", sa.Text(), nullable=True),
        sa.Column(
            "evidence_location",
            geoalchemy2.Geometry(
                geometry_type="POINT",
                srid=4326,
                spatial_index=False,
            ),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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
            ["inspection_id", "enterprise_id"],
            ["field_inspections.id", "field_inspections.enterprise_id"],
            name="fk_inspection_results_inspection_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["field_id"],
            ["fields.id"],
            name="fk_inspection_results_field",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id"],
            ["enterprises.id"],
            name="fk_inspection_results_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_id"],
            ["users.id"],
            name="fk_inspection_results_recorded_by",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "inspection_id",
            name="uq_inspection_results_inspection_id",
        ),
        sa.UniqueConstraint(
            "id",
            "enterprise_id",
            name="uq_inspection_results_id_enterprise_id",
        ),
        sa.CheckConstraint(
            f"cause_code IN ({quoted(CAUSE_CODES)})",
            name="ck_inspection_results_cause_code",
        ),
        sa.CheckConstraint(
            "cause_code <> 'other' OR "
            "(cause_details IS NOT NULL AND btrim(cause_details) <> '')",
            name="ck_inspection_results_other_details",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_inspection_results_version",
        ),
    )
    op.create_index(
        "ix_inspection_results_enterprise_created",
        "inspection_results",
        ["enterprise_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_inspection_results_evidence_location",
        "inspection_results",
        ["evidence_location"],
        postgresql_using="gist",
    )

    op.create_table(
        "inspection_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("inspection_id", sa.Integer(), nullable=False),
        sa.Column("result_id", sa.Integer(), nullable=True),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("evidence_type", sa.String(20), nullable=False),
        sa.Column(
            "provider",
            sa.String(40),
            nullable=False,
            server_default="metadata_only",
        ),
        sa.Column("provider_reference", sa.String(255), nullable=True),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("media_type", sa.String(100), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "location",
            geoalchemy2.Geometry(
                geometry_type="POINT",
                srid=4326,
                spatial_index=False,
            ),
            nullable=True,
        ),
        sa.Column(
            "provider_metadata",
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
            ["inspection_id", "enterprise_id"],
            ["field_inspections.id", "field_inspections.enterprise_id"],
            name="fk_inspection_evidence_inspection_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_id", "enterprise_id"],
            ["inspection_results.id", "inspection_results.enterprise_id"],
            name="fk_inspection_evidence_result_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["field_id"],
            ["fields.id"],
            name="fk_inspection_evidence_field",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id"],
            ["enterprises.id"],
            name="fk_inspection_evidence_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_inspection_evidence_created_by",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "evidence_type IN ('photo','note','geolocation')",
            name="ck_inspection_evidence_type",
        ),
        sa.CheckConstraint(
            "byte_size IS NULL OR byte_size BETWEEN 0 AND 26214400",
            name="ck_inspection_evidence_byte_size",
        ),
        sa.CheckConstraint(
            "sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_inspection_evidence_sha256",
        ),
        sa.CheckConstraint(
            "evidence_type <> 'photo' OR "
            "(original_filename IS NOT NULL AND media_type IS NOT NULL "
            "AND byte_size IS NOT NULL AND sha256 IS NOT NULL)",
            name="ck_inspection_evidence_photo_metadata",
        ),
    )
    op.create_index(
        "ix_inspection_evidence_inspection_created",
        "inspection_evidence",
        ["inspection_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_inspection_evidence_location",
        "inspection_evidence",
        ["location"],
        postgresql_using="gist",
    )

    op.create_table(
        "corrective_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("inspection_id", sa.Integer(), nullable=False),
        sa.Column("result_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="open",
        ),
        sa.Column("closure_reason", sa.Text(), nullable=True),
        sa.Column("closed_by_id", sa.Integer(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopen_reason", sa.Text(), nullable=True),
        sa.Column("reopened_by_id", sa.Integer(), nullable=True),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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
            ["inspection_id", "enterprise_id"],
            ["field_inspections.id", "field_inspections.enterprise_id"],
            name="fk_corrective_actions_inspection_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_id", "enterprise_id"],
            ["inspection_results.id", "inspection_results.enterprise_id"],
            name="fk_corrective_actions_result_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["field_id"],
            ["fields.id"],
            name="fk_corrective_actions_field",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id"],
            ["enterprises.id"],
            name="fk_corrective_actions_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_corrective_actions_created_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_corrective_actions_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["closed_by_id"],
            ["users.id"],
            name="fk_corrective_actions_closed_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reopened_by_id"],
            ["users.id"],
            name="fk_corrective_actions_reopened_by",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "enterprise_id",
            name="uq_corrective_actions_id_enterprise_id",
        ),
        sa.CheckConstraint(
            f"status IN ({quoted(ACTION_STATUSES)})",
            name="ck_corrective_actions_status",
        ),
        sa.CheckConstraint(
            "btrim(description) <> ''",
            name="ck_corrective_actions_description",
        ),
        sa.CheckConstraint(
            "(status = 'closed' AND closure_reason IS NOT NULL "
            "AND btrim(closure_reason) <> '' AND closed_by_id IS NOT NULL "
            "AND closed_at IS NOT NULL) OR "
            "(status <> 'closed' AND closure_reason IS NULL "
            "AND closed_by_id IS NULL AND closed_at IS NULL)",
            name="ck_corrective_actions_closure_state",
        ),
        sa.CheckConstraint(
            "(reopen_reason IS NULL AND reopened_by_id IS NULL "
            "AND reopened_at IS NULL) OR "
            "(reopen_reason IS NOT NULL AND btrim(reopen_reason) <> '' "
            "AND reopened_by_id IS NOT NULL AND reopened_at IS NOT NULL)",
            name="ck_corrective_actions_reopen_state",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_corrective_actions_version",
        ),
    )
    op.create_index(
        "ix_corrective_actions_enterprise_status_due",
        "corrective_actions",
        ["enterprise_id", "status", "due_date"],
    )
    op.create_index(
        "ix_corrective_actions_owner_status_due",
        "corrective_actions",
        ["owner_id", "status", "due_date"],
    )
    op.create_index(
        "ix_corrective_actions_inspection_created",
        "corrective_actions",
        ["inspection_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "action_verification_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_id", sa.Integer(), nullable=False),
        sa.Column("index_code", sa.String(20), nullable=False),
        sa.Column("reference_date", sa.Date(), nullable=False),
        sa.Column(
            "minimum_separation_days",
            sa.SmallInteger(),
            nullable=False,
            server_default="3",
        ),
        sa.Column(
            "status",
            sa.String(30),
            nullable=False,
            server_default="awaiting_observation",
        ),
        sa.Column("reference_ndvi_record_id", sa.Integer(), nullable=True),
        sa.Column(
            "reference_satellite_record_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column("observation_ndvi_record_id", sa.Integer(), nullable=True),
        sa.Column(
            "observation_satellite_record_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column("reference_value", sa.Float(), nullable=True),
        sa.Column("observation_value", sa.Float(), nullable=True),
        sa.Column("delta_value", sa.Float(), nullable=True),
        sa.Column("reference_observed_at", sa.Date(), nullable=True),
        sa.Column("observation_observed_at", sa.Date(), nullable=True),
        sa.Column("reference_valid_pixels_pct", sa.Float(), nullable=True),
        sa.Column("reference_cloud_cover_pct", sa.Float(), nullable=True),
        sa.Column("reference_satellite", sa.String(50), nullable=True),
        sa.Column("observation_valid_pixels_pct", sa.Float(), nullable=True),
        sa.Column("observation_cloud_cover_pct", sa.Float(), nullable=True),
        sa.Column("observation_satellite", sa.String(50), nullable=True),
        sa.Column("result", sa.String(30), nullable=True),
        sa.Column("confidence", sa.String(20), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "verification_provenance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "algorithm_version",
            sa.String(50),
            nullable=False,
            server_default="observation_direction_v1",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["action_id", "enterprise_id"],
            ["corrective_actions.id", "corrective_actions.enterprise_id"],
            name="fk_action_verification_action_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["field_id"],
            ["fields.id"],
            name="fk_action_verification_field",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id"],
            ["enterprises.id"],
            name="fk_action_verification_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name="fk_action_verification_requested_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reference_ndvi_record_id"],
            ["ndvi_records.id"],
            name="fk_action_verification_reference_ndvi",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reference_satellite_record_id"],
            ["satellite_index_records.id"],
            name="fk_action_verification_reference_satellite",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["observation_ndvi_record_id"],
            ["ndvi_records.id"],
            name="fk_action_verification_observation_ndvi",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["observation_satellite_record_id"],
            ["satellite_index_records.id"],
            name="fk_action_verification_observation_satellite",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "enterprise_id",
            name="uq_action_verification_id_enterprise_id",
        ),
        sa.CheckConstraint(
            f"index_code IN ({quoted(INDEX_CODES)})",
            name="ck_action_verification_index_code",
        ),
        sa.CheckConstraint(
            "minimum_separation_days BETWEEN 1 AND 30",
            name="ck_action_verification_minimum_separation",
        ),
        sa.CheckConstraint(
            "status IN ('awaiting_observation','resolved')",
            name="ck_action_verification_status",
        ),
        sa.CheckConstraint(
            f"result IS NULL OR result IN ({quoted(VERIFICATION_RESULTS)})",
            name="ck_action_verification_result",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence IN ('low','medium','high')",
            name="ck_action_verification_confidence",
        ),
        sa.CheckConstraint(
            "(index_code = 'ndvi' "
            "AND reference_satellite_record_id IS NULL "
            "AND observation_satellite_record_id IS NULL) OR "
            "(index_code <> 'ndvi' "
            "AND reference_ndvi_record_id IS NULL "
            "AND observation_ndvi_record_id IS NULL)",
            name="ck_action_verification_record_type",
        ),
        sa.CheckConstraint(
            "(reference_value IS NULL AND reference_observed_at IS NULL "
            "AND reference_valid_pixels_pct IS NULL "
            "AND reference_cloud_cover_pct IS NULL "
            "AND reference_satellite IS NULL "
            "AND reference_ndvi_record_id IS NULL "
            "AND reference_satellite_record_id IS NULL) OR "
            "(reference_value IS NOT NULL AND reference_observed_at IS NOT NULL "
            "AND reference_valid_pixels_pct IS NOT NULL "
            "AND reference_cloud_cover_pct IS NOT NULL "
            "AND reference_satellite IS NOT NULL "
            "AND ((reference_ndvi_record_id IS NOT NULL) <> "
            "(reference_satellite_record_id IS NOT NULL)))",
            name="ck_action_verification_reference_shape",
        ),
        sa.CheckConstraint(
            "(observation_value IS NULL AND delta_value IS NULL "
            "AND observation_observed_at IS NULL "
            "AND observation_valid_pixels_pct IS NULL "
            "AND observation_cloud_cover_pct IS NULL "
            "AND observation_satellite IS NULL "
            "AND observation_ndvi_record_id IS NULL "
            "AND observation_satellite_record_id IS NULL) OR "
            "(observation_value IS NOT NULL AND delta_value IS NOT NULL "
            "AND observation_observed_at IS NOT NULL "
            "AND observation_valid_pixels_pct IS NOT NULL "
            "AND observation_cloud_cover_pct IS NOT NULL "
            "AND observation_satellite IS NOT NULL "
            "AND ((observation_ndvi_record_id IS NOT NULL) <> "
            "(observation_satellite_record_id IS NOT NULL)))",
            name="ck_action_verification_observation_shape",
        ),
        sa.CheckConstraint(
            "(reference_valid_pixels_pct IS NULL OR "
            "reference_valid_pixels_pct BETWEEN 0 AND 100) AND "
            "(reference_cloud_cover_pct IS NULL OR "
            "reference_cloud_cover_pct BETWEEN 0 AND 100) AND "
            "(observation_valid_pixels_pct IS NULL OR "
            "observation_valid_pixels_pct BETWEEN 0 AND 100) AND "
            "(observation_cloud_cover_pct IS NULL OR "
            "observation_cloud_cover_pct BETWEEN 0 AND 100)",
            name="ck_action_verification_quality_ranges",
        ),
        sa.CheckConstraint(
            "(status = 'awaiting_observation' AND result IS NULL "
            "AND confidence IS NULL AND resolved_at IS NULL "
            "AND observation_value IS NULL) OR "
            "(status = 'resolved' AND result IS NOT NULL "
            "AND confidence IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_action_verification_resolution_state",
        ),
        sa.CheckConstraint(
            "result IS NULL OR result = 'insufficient_data' OR "
            "(reference_value IS NOT NULL AND observation_value IS NOT NULL)",
            name="ck_action_verification_result_observations",
        ),
        sa.CheckConstraint(
            "observation_observed_at IS NULL OR "
            "(reference_observed_at IS NOT NULL "
            "AND observation_observed_at > reference_observed_at "
            "AND observation_observed_at >= "
            "reference_date + minimum_separation_days)",
            name="ck_action_verification_temporal_order",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_action_verification_version",
        ),
    )
    op.create_index(
        "uq_action_verification_one_awaiting",
        "action_verification_requests",
        ["action_id"],
        unique=True,
        postgresql_where=sa.text("status = 'awaiting_observation'"),
    )
    op.create_index(
        "ix_action_verification_enterprise_status_requested",
        "action_verification_requests",
        ["enterprise_id", "status", sa.text("requested_at DESC")],
    )
    op.create_index(
        "ix_action_verification_action_requested",
        "action_verification_requests",
        ["action_id", sa.text("requested_at DESC")],
    )

    op.create_table(
        "operational_audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("inspection_id", sa.Integer(), nullable=False),
        sa.Column("action_id", sa.Integer(), nullable=True),
        sa.Column("verification_id", sa.Integer(), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("entity_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "event_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["inspection_id", "enterprise_id"],
            ["field_inspections.id", "field_inspections.enterprise_id"],
            name="fk_operational_audit_inspection_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["action_id", "enterprise_id"],
            ["corrective_actions.id", "corrective_actions.enterprise_id"],
            name="fk_operational_audit_action_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["verification_id", "enterprise_id"],
            [
                "action_verification_requests.id",
                "action_verification_requests.enterprise_id",
            ],
            name="fk_operational_audit_verification_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["field_id"],
            ["fields.id"],
            name="fk_operational_audit_field",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id"],
            ["enterprises.id"],
            name="fk_operational_audit_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_operational_audit_actor",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "actor_id",
            "idempotency_key",
            name="uq_operational_audit_actor_idempotency_key",
        ),
        sa.CheckConstraint(
            f"event_type IN ({quoted(AUDIT_EVENTS)})",
            name="ck_operational_audit_event_type",
        ),
        sa.CheckConstraint(
            "entity_version >= 1",
            name="ck_operational_audit_entity_version",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_operational_audit_request_fingerprint",
        ),
    )
    op.create_index(
        "ix_operational_audit_inspection_occurred",
        "operational_audit_events",
        ["inspection_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_operational_audit_action_occurred",
        "operational_audit_events",
        ["action_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_operational_audit_enterprise_occurred",
        "operational_audit_events",
        ["enterprise_id", sa.text("occurred_at DESC"), sa.text("id DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_operational_audit_enterprise_occurred",
        table_name="operational_audit_events",
    )
    op.drop_index(
        "ix_operational_audit_action_occurred",
        table_name="operational_audit_events",
    )
    op.drop_index(
        "ix_operational_audit_inspection_occurred",
        table_name="operational_audit_events",
    )
    op.drop_table("operational_audit_events")

    op.drop_index(
        "ix_action_verification_action_requested",
        table_name="action_verification_requests",
    )
    op.drop_index(
        "ix_action_verification_enterprise_status_requested",
        table_name="action_verification_requests",
    )
    op.drop_index(
        "uq_action_verification_one_awaiting",
        table_name="action_verification_requests",
    )
    op.drop_table("action_verification_requests")

    op.drop_index(
        "ix_corrective_actions_inspection_created",
        table_name="corrective_actions",
    )
    op.drop_index(
        "ix_corrective_actions_owner_status_due",
        table_name="corrective_actions",
    )
    op.drop_index(
        "ix_corrective_actions_enterprise_status_due",
        table_name="corrective_actions",
    )
    op.drop_table("corrective_actions")

    op.drop_index(
        "ix_inspection_evidence_location",
        table_name="inspection_evidence",
    )
    op.drop_index(
        "ix_inspection_evidence_inspection_created",
        table_name="inspection_evidence",
    )
    op.drop_table("inspection_evidence")

    op.drop_index(
        "ix_inspection_results_evidence_location",
        table_name="inspection_results",
    )
    op.drop_index(
        "ix_inspection_results_enterprise_created",
        table_name="inspection_results",
    )
    op.drop_table("inspection_results")

    op.drop_constraint(
        "uq_field_inspections_id_enterprise_id",
        "field_inspections",
        type_="unique",
    )

    _restore_alembic_version_capacity()
