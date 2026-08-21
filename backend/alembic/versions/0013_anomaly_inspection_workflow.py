"""Extend inspections through anomaly review, action, and verification.

Revision ID: 0013_anomaly_inspection_workflow
Revises: 0012_commercial_tenant_boundary
"""

from typing import Sequence, Union

from alembic import op
import geoalchemy2
import sqlalchemy as sa


revision: str = "0013_anomaly_inspection_workflow"
down_revision: Union[str, Sequence[str], None] = "0012_commercial_tenant_boundary"
branch_labels = None
depends_on = None


INSPECTION_STATUSES = (
    "pending", "new", "assigned", "in_progress", "submitted", "completed",
    "confirmed", "rejected", "cancelled",
)
SOURCE_KINDS = ("legacy", "pixel_ndvi", "alert", "manual")
PRIORITIES = ("low", "normal", "high", "urgent")
CAUSE_CODES = (
    "water_stress", "irrigation_failure", "pest", "disease", "nutrient_deficiency",
    "weed_pressure", "mechanical_damage", "soil_salinity", "weather_damage",
    "false_positive", "other", "unconfirmed", "irrigation", "nutrient", "weather",
    "soil", "mechanical", "crop_stage", "no_issue",
)
SEVERITIES = ("none", "low", "moderate", "high", "critical")
SYNC_STATES = ("server", "local_draft", "pending_sync", "conflict")
ACTION_STATUSES = (
    "planned", "in_progress", "completed", "verified_effective",
    "verified_ineffective", "cancelled", "open", "blocked", "closed",
)
VERIFICATION_RESULTS = ("effective", "ineffective", "another_cycle")
AUDIT_EVENTS = (
    "inspection_result_recorded", "evidence_attached", "action_created", "action_updated",
    "action_closed", "action_reopened", "verification_requested", "verification_resolved",
    "inspection_created", "inspection_assigned", "inspection_reassigned", "inspection_started",
    "finding_saved", "inspection_submitted", "inspection_confirmed", "inspection_rejected",
    "inspection_cancelled", "photo_uploaded", "photo_deleted", "action_started",
    "action_completed", "action_cancelled", "action_verified_effective",
    "action_verified_ineffective", "follow_up_created",
)


def quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.drop_index("uq_field_inspections_one_active_per_field", table_name="field_inspections")
    op.drop_constraint("ck_field_inspections_status", "field_inspections", type_="check")
    op.drop_constraint("ck_field_inspections_source", "field_inspections", type_="check")
    op.drop_constraint("ck_field_inspections_source_priority", "field_inspections", type_="check")
    op.drop_constraint("ck_field_inspections_terminal_timestamps", "field_inspections", type_="check")

    inspection_columns = (
        sa.Column("source_kind", sa.String(20), nullable=True),
        sa.Column("source_alert_id", sa.Integer(), nullable=True),
        sa.Column("source_provider", sa.String(40), nullable=True),
        sa.Column("source_item_id", sa.String(768), nullable=True),
        sa.Column("source_acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_index_name", sa.String(20), nullable=True),
        sa.Column("source_sampled_value", sa.Float(), nullable=True),
        sa.Column("source_comparison_value", sa.Float(), nullable=True),
        sa.Column("source_delta", sa.Float(), nullable=True),
        sa.Column("source_geometry_hash", sa.String(64), nullable=True),
        sa.Column(
            "source_point",
            geoalchemy2.Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column(
            "source_zone",
            geoalchemy2.Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column("source_reason", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(20), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_by_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reassignment_reason", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("follow_up_of_id", sa.Integer(), nullable=True),
        sa.Column("source_snapshot_locked", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    for column in inspection_columns:
        op.add_column("field_inspections", column)

    op.execute(
        """
        UPDATE field_inspections
           SET source_kind = 'legacy',
               source_reason = COALESCE(NULLIF(btrim(instructions), ''), title),
               priority = CASE source_priority
                   WHEN 'medium' THEN 'normal' WHEN 'critical' THEN 'urgent'
                   WHEN 'low' THEN 'low' WHEN 'high' THEN 'high' ELSE 'normal' END,
               due_at = CASE WHEN due_date IS NULL THEN NULL ELSE
                   (due_date::timestamp + interval '23 hours 59 minutes') AT TIME ZONE 'Asia/Tashkent' END,
               updated_by_id = created_by_id,
               reviewed_by_id = NULL,
               reviewed_at = NULL,
               review_reason = NULL,
               confirmed_at = NULL
        """
    )
    op.alter_column("field_inspections", "source_kind", nullable=False)
    op.alter_column("field_inspections", "source_reason", nullable=False)
    op.alter_column("field_inspections", "priority", nullable=False, server_default="normal")

    op.create_foreign_key(
        "fk_field_inspections_source_alert", "field_inspections", "alerts",
        ["source_alert_id"], ["id"], ondelete="RESTRICT",
    )
    for name, column in (
        ("fk_field_inspections_updated_by", "updated_by_id"),
        ("fk_field_inspections_reviewed_by", "reviewed_by_id"),
    ):
        op.create_foreign_key(name, "field_inspections", "users", [column], ["id"], ondelete="RESTRICT")
    op.create_foreign_key(
        "fk_field_inspections_follow_up", "field_inspections", "field_inspections",
        ["follow_up_of_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_field_inspections_status", "field_inspections",
        f"status IN ({quoted(INSPECTION_STATUSES)})",
    )
    op.create_check_constraint(
        "ck_field_inspections_source", "field_inspections",
        f"source_kind IN ({quoted(SOURCE_KINDS)})",
    )
    op.create_check_constraint(
        "ck_field_inspections_legacy_source", "field_inspections",
        "source_kind <> 'legacy' OR source IN ('attention_queue','manual','irrigation_context')",
    )
    op.create_check_constraint(
        "ck_field_inspections_source_priority", "field_inspections",
        f"priority IN ({quoted(PRIORITIES)})",
    )
    op.create_check_constraint(
        "ck_field_inspections_source_values", "field_inspections",
        "(source_sampled_value IS NULL OR source_sampled_value BETWEEN -1 AND 1) AND "
        "(source_comparison_value IS NULL OR source_comparison_value BETWEEN -1 AND 1) AND "
        "(source_delta IS NULL OR source_delta BETWEEN -2 AND 2)",
    )
    op.create_check_constraint(
        "ck_field_inspections_source_geometry", "field_inspections",
        "NOT (source_point IS NOT NULL AND source_zone IS NOT NULL) AND "
        "(source_point IS NULL OR (NOT ST_IsEmpty(source_point) AND ST_IsValid(source_point))) AND "
        "(source_zone IS NULL OR (NOT ST_IsEmpty(source_zone) AND ST_IsValid(source_zone)))",
    )
    op.create_check_constraint(
        "ck_field_inspections_pixel_snapshot", "field_inspections",
        "source_kind <> 'pixel_ndvi' OR (source_provider IS NOT NULL AND source_item_id IS NOT NULL "
        "AND source_acquired_at IS NOT NULL AND source_index_name IS NOT NULL "
        "AND source_sampled_value IS NOT NULL AND source_geometry_hash IS NOT NULL "
        "AND (source_point IS NOT NULL OR source_zone IS NOT NULL))",
    )
    op.create_check_constraint(
        "ck_field_inspections_alert_snapshot", "field_inspections",
        "source_kind <> 'alert' OR source_alert_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_field_inspections_assignment_state", "field_inspections",
        "source_kind = 'legacy' OR status = 'new' OR assigned_to_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_field_inspections_review_state", "field_inspections",
        "source_kind = 'legacy' OR (status = 'confirmed' AND reviewed_by_id IS NOT NULL AND reviewed_at IS NOT NULL "
        "AND confirmed_at IS NOT NULL AND rejected_at IS NULL) OR "
        "(status = 'rejected' AND reviewed_by_id IS NOT NULL AND reviewed_at IS NOT NULL "
        "AND review_reason IS NOT NULL AND rejected_at IS NOT NULL AND confirmed_at IS NULL) OR "
        "(status NOT IN ('confirmed','rejected') AND confirmed_at IS NULL AND rejected_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_field_inspections_submit_state", "field_inspections",
        "source_kind = 'legacy' OR status NOT IN ('submitted','confirmed','rejected') OR submitted_at IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_field_inspections_cancel_state", "field_inspections",
        "(source_kind = 'legacy' AND ((status = 'completed' AND completed_at IS NOT NULL AND cancelled_at IS NULL) OR "
        "(status = 'cancelled' AND cancelled_at IS NOT NULL AND completed_at IS NULL) OR "
        "(status IN ('pending','in_progress') AND completed_at IS NULL AND cancelled_at IS NULL))) OR "
        "(source_kind <> 'legacy' AND (status <> 'cancelled' OR "
        "(cancelled_at IS NOT NULL AND cancellation_reason IS NOT NULL)))",
    )
    op.create_index(
        "ix_field_inspections_queue", "field_inspections",
        ["enterprise_id", "status", "priority", "due_at", sa.text("id DESC")],
    )
    op.create_index(
        "ix_field_inspections_source_point", "field_inspections", ["source_point"],
        postgresql_using="gist",
    )
    op.create_index(
        "ix_field_inspections_source_zone", "field_inspections", ["source_zone"],
        postgresql_using="gist",
    )
    op.create_index(
        "ix_field_inspections_alert", "field_inspections", ["source_alert_id"],
        unique=True, postgresql_where=sa.text("source_alert_id IS NOT NULL"),
    )
    op.create_index(
        "uq_field_inspections_one_active_legacy_per_field", "field_inspections", ["field_id"],
        unique=True, postgresql_where=sa.text("source_kind='legacy' AND status IN ('pending','in_progress')"),
    )

    op.drop_constraint("ck_inspection_results_cause_code", "inspection_results", type_="check")
    op.drop_constraint("ck_inspection_results_other_details", "inspection_results", type_="check")
    result_columns = (
        sa.Column("actual_inspected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gps_accuracy_m", sa.Float(), nullable=True),
        sa.Column("severity", sa.String(20), nullable=True),
        sa.Column("affected_area_ha", sa.Float(), nullable=True),
        sa.Column("affected_area_pct", sa.Float(), nullable=True),
        sa.Column("observations", sa.Text(), nullable=True),
        sa.Column("recommended_action", sa.Text(), nullable=True),
        sa.Column("sync_state", sa.String(20), nullable=False, server_default="server"),
    )
    for column in result_columns:
        op.add_column("inspection_results", column)
    op.create_check_constraint(
        "ck_inspection_results_cause_code", "inspection_results",
        f"cause_code IN ({quoted(CAUSE_CODES)})",
    )
    op.create_check_constraint(
        "ck_inspection_results_other_details", "inspection_results",
        "cause_code <> 'other' OR (cause_details IS NOT NULL AND btrim(cause_details) <> '')",
    )
    op.create_check_constraint(
        "ck_inspection_results_severity", "inspection_results",
        f"severity IS NULL OR severity IN ({quoted(SEVERITIES)})",
    )
    op.create_check_constraint(
        "ck_inspection_results_affected_area", "inspection_results",
        "(affected_area_ha IS NULL OR affected_area_ha BETWEEN 0 AND 1000000) AND "
        "(affected_area_pct IS NULL OR affected_area_pct BETWEEN 0 AND 100) AND "
        "NOT (affected_area_ha IS NOT NULL AND affected_area_pct IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_inspection_results_gps_accuracy", "inspection_results",
        "gps_accuracy_m IS NULL OR gps_accuracy_m BETWEEN 0 AND 10000",
    )
    op.create_check_constraint(
        "ck_inspection_results_sync_state", "inspection_results",
        f"sync_state IN ({quoted(SYNC_STATES)})",
    )

    evidence_columns = (
        sa.Column("storage_key", sa.String(255), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_id", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    for column in evidence_columns:
        op.add_column("inspection_evidence", column)
    op.create_foreign_key(
        "fk_inspection_evidence_deleted_by", "inspection_evidence", "users",
        ["deleted_by_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_inspection_evidence_delete_state", "inspection_evidence",
        "(deleted_at IS NULL AND deleted_by_id IS NULL) OR "
        "(deleted_at IS NOT NULL AND deleted_by_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_inspection_evidence_storage_key", "inspection_evidence",
        "storage_key IS NULL OR storage_key ~ '^[0-9a-f]{2}/[0-9a-f-]{36}\\.(jpg|png|webp)$'",
    )

    op.drop_constraint("ck_corrective_actions_status", "corrective_actions", type_="check")
    op.drop_constraint("ck_corrective_actions_closure_state", "corrective_actions", type_="check")
    action_columns = (
        sa.Column("action_type", sa.String(50), nullable=True),
        sa.Column("planned_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_note", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_id", sa.Integer(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_result", sa.String(30), nullable=True),
        sa.Column("verification_notes", sa.Text(), nullable=True),
        sa.Column("verification_index_name", sa.String(20), nullable=True),
        sa.Column("verification_sample_value", sa.Float(), nullable=True),
        sa.Column("follow_up_inspection_id", sa.Integer(), nullable=True),
    )
    for column in action_columns:
        op.add_column("corrective_actions", column)
    op.create_foreign_key(
        "fk_corrective_actions_verified_by", "corrective_actions", "users",
        ["verified_by_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_corrective_actions_follow_up", "corrective_actions", "field_inspections",
        ["follow_up_inspection_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_corrective_actions_status", "corrective_actions",
        f"status IN ({quoted(ACTION_STATUSES)})",
    )
    op.create_check_constraint(
        "ck_corrective_actions_legacy_closure_state", "corrective_actions",
        "action_type IS NOT NULL OR ((status = 'closed' AND closure_reason IS NOT NULL "
        "AND btrim(closure_reason) <> '' AND closed_by_id IS NOT NULL AND closed_at IS NOT NULL) OR "
        "(status <> 'closed' AND closure_reason IS NULL AND closed_by_id IS NULL AND closed_at IS NULL))",
    )
    op.create_check_constraint(
        "ck_corrective_actions_completion_state", "corrective_actions",
        "action_type IS NULL OR status NOT IN ('completed','verified_effective','verified_ineffective') OR "
        "(completion_note IS NOT NULL AND completed_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_corrective_actions_cancel_state", "corrective_actions",
        "action_type IS NULL OR status <> 'cancelled' OR (cancelled_reason IS NOT NULL AND cancelled_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_corrective_actions_verification_state", "corrective_actions",
        "action_type IS NULL OR (status = 'verified_effective' AND verification_result = 'effective' "
        "AND verified_by_id IS NOT NULL AND verified_at IS NOT NULL) OR "
        "(status = 'verified_ineffective' AND verification_result IN ('ineffective','another_cycle') "
        "AND verified_by_id IS NOT NULL AND verified_at IS NOT NULL) OR "
        "(status NOT IN ('verified_effective','verified_ineffective') AND verification_result IS NULL "
        "AND verified_by_id IS NULL AND verified_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_corrective_actions_verification_value", "corrective_actions",
        "verification_sample_value IS NULL OR verification_sample_value BETWEEN -1 AND 1",
    )
    op.create_index(
        "ix_corrective_actions_verification_due", "corrective_actions",
        ["enterprise_id", "status", "due_at", sa.text("id DESC")],
    )

    op.drop_constraint("ck_operational_audit_event_type", "operational_audit_events", type_="check")
    op.alter_column(
        "operational_audit_events", "event_type",
        existing_type=sa.String(50), type_=sa.String(80), existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_operational_audit_event_type", "operational_audit_events",
        f"event_type IN ({quoted(AUDIT_EVENTS)})",
    )
    op.execute(
        """
        CREATE FUNCTION reject_operational_audit_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          RAISE EXCEPTION 'operational audit events are immutable';
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_operational_audit_immutable
        BEFORE UPDATE OR DELETE ON operational_audit_events
        FOR EACH ROW EXECUTE FUNCTION reject_operational_audit_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_operational_audit_immutable ON operational_audit_events")
    op.execute("DROP FUNCTION IF EXISTS reject_operational_audit_mutation()")
    op.drop_constraint("ck_operational_audit_event_type", "operational_audit_events", type_="check")
    op.alter_column(
        "operational_audit_events", "event_type",
        existing_type=sa.String(80), type_=sa.String(50), existing_nullable=False,
    )
    legacy_events = (
        "inspection_result_recorded", "evidence_attached", "action_created", "action_updated",
        "action_closed", "action_reopened", "verification_requested", "verification_resolved",
    )
    op.execute(
        "DELETE FROM operational_audit_events WHERE event_type NOT IN "
        f"({quoted(legacy_events)})"
    )
    op.create_check_constraint(
        "ck_operational_audit_event_type", "operational_audit_events",
        f"event_type IN ({quoted(legacy_events)})",
    )

    op.drop_index("ix_corrective_actions_verification_due", table_name="corrective_actions")
    for name in (
        "ck_corrective_actions_verification_value", "ck_corrective_actions_verification_state",
        "ck_corrective_actions_cancel_state", "ck_corrective_actions_completion_state",
        "ck_corrective_actions_legacy_closure_state", "ck_corrective_actions_status",
    ):
        op.drop_constraint(name, "corrective_actions", type_="check")
    op.execute(
        """
        UPDATE corrective_actions SET status = CASE status
          WHEN 'planned' THEN 'open' WHEN 'completed' THEN 'closed'
          WHEN 'verified_effective' THEN 'closed' WHEN 'verified_ineffective' THEN 'closed'
          WHEN 'cancelled' THEN 'blocked' ELSE status END,
          closure_reason = COALESCE(closure_reason, completion_note, cancelled_reason, 'TASK_217 downgrade'),
          closed_by_id = CASE WHEN status IN ('completed','verified_effective','verified_ineffective')
            THEN COALESCE(closed_by_id, verified_by_id, created_by_id) ELSE NULL END,
          closed_at = CASE WHEN status IN ('completed','verified_effective','verified_ineffective')
            THEN COALESCE(closed_at, completed_at, verified_at, now()) ELSE NULL END
        WHERE action_type IS NOT NULL
        """
    )
    op.drop_constraint("fk_corrective_actions_follow_up", "corrective_actions", type_="foreignkey")
    op.drop_constraint("fk_corrective_actions_verified_by", "corrective_actions", type_="foreignkey")
    for column in (
        "follow_up_inspection_id", "verification_sample_value", "verification_index_name",
        "verification_notes", "verification_result", "verified_at", "verified_by_id", "cancelled_at",
        "cancelled_reason", "completed_at", "completion_note", "started_at", "due_at",
        "planned_start_at", "action_type",
    ):
        op.drop_column("corrective_actions", column)
    op.create_check_constraint(
        "ck_corrective_actions_status", "corrective_actions",
        "status IN ('open','in_progress','blocked','closed')",
    )
    op.create_check_constraint(
        "ck_corrective_actions_closure_state", "corrective_actions",
        "(status = 'closed' AND closure_reason IS NOT NULL AND btrim(closure_reason) <> '' "
        "AND closed_by_id IS NOT NULL AND closed_at IS NOT NULL) OR "
        "(status <> 'closed' AND closure_reason IS NULL AND closed_by_id IS NULL AND closed_at IS NULL)",
    )

    for name in (
        "ck_inspection_evidence_storage_key", "ck_inspection_evidence_delete_state",
    ):
        op.drop_constraint(name, "inspection_evidence", type_="check")
    op.drop_constraint("fk_inspection_evidence_deleted_by", "inspection_evidence", type_="foreignkey")
    for column in ("version", "deleted_by_id", "deleted_at", "storage_key"):
        op.drop_column("inspection_evidence", column)

    for name in (
        "ck_inspection_results_sync_state", "ck_inspection_results_gps_accuracy",
        "ck_inspection_results_affected_area", "ck_inspection_results_severity",
        "ck_inspection_results_other_details", "ck_inspection_results_cause_code",
    ):
        op.drop_constraint(name, "inspection_results", type_="check")
    op.execute(
        """
        UPDATE inspection_results SET cause_code = CASE cause_code
          WHEN 'irrigation_failure' THEN 'irrigation' WHEN 'water_stress' THEN 'irrigation'
          WHEN 'nutrient_deficiency' THEN 'nutrient' WHEN 'weather_damage' THEN 'weather'
          WHEN 'soil_salinity' THEN 'soil' WHEN 'mechanical_damage' THEN 'mechanical'
          WHEN 'false_positive' THEN 'no_issue' WHEN 'weed_pressure' THEN 'other' ELSE cause_code END,
          cause_details = CASE WHEN cause_code = 'weed_pressure' AND cause_details IS NULL
            THEN 'Weed pressure' ELSE cause_details END
        """
    )
    for column in (
        "sync_state", "recommended_action", "observations", "affected_area_pct",
        "affected_area_ha", "severity", "gps_accuracy_m", "actual_inspected_at",
    ):
        op.drop_column("inspection_results", column)
    op.create_check_constraint(
        "ck_inspection_results_cause_code", "inspection_results",
        "cause_code IN ('irrigation','pest','disease','nutrient','weather','soil','mechanical',"
        "'crop_stage','no_issue','other','unconfirmed')",
    )
    op.create_check_constraint(
        "ck_inspection_results_other_details", "inspection_results",
        "cause_code <> 'other' OR (cause_details IS NOT NULL AND btrim(cause_details) <> '')",
    )

    op.drop_index("ix_field_inspections_alert", table_name="field_inspections")
    op.drop_index("uq_field_inspections_one_active_legacy_per_field", table_name="field_inspections")
    op.drop_index("ix_field_inspections_source_zone", table_name="field_inspections")
    op.drop_index("ix_field_inspections_source_point", table_name="field_inspections")
    op.drop_index("ix_field_inspections_queue", table_name="field_inspections")
    for name in (
        "ck_field_inspections_cancel_state", "ck_field_inspections_submit_state",
        "ck_field_inspections_review_state", "ck_field_inspections_assignment_state",
        "ck_field_inspections_alert_snapshot", "ck_field_inspections_pixel_snapshot",
        "ck_field_inspections_source_geometry", "ck_field_inspections_source_values",
        "ck_field_inspections_source_priority", "ck_field_inspections_legacy_source",
        "ck_field_inspections_source",
        "ck_field_inspections_status",
    ):
        op.drop_constraint(name, "field_inspections", type_="check")
    op.execute(
        """
        UPDATE field_inspections SET
          status = CASE status WHEN 'new' THEN 'pending' WHEN 'assigned' THEN 'pending'
            WHEN 'submitted' THEN 'in_progress' WHEN 'confirmed' THEN 'completed'
            WHEN 'rejected' THEN 'completed' ELSE status END,
          source_priority = CASE priority WHEN 'normal' THEN 'medium' WHEN 'urgent' THEN 'critical'
            ELSE priority END,
          completed_at = CASE WHEN status IN ('confirmed','rejected')
            THEN COALESCE(completed_at, reviewed_at, now()) ELSE completed_at END,
          completion_summary = CASE WHEN status IN ('confirmed','rejected')
            THEN COALESCE(completion_summary, review_reason, 'TASK_217 downgrade') ELSE completion_summary END
        """
    )
    op.drop_constraint("fk_field_inspections_follow_up", "field_inspections", type_="foreignkey")
    op.drop_constraint("fk_field_inspections_reviewed_by", "field_inspections", type_="foreignkey")
    op.drop_constraint("fk_field_inspections_updated_by", "field_inspections", type_="foreignkey")
    op.drop_constraint("fk_field_inspections_source_alert", "field_inspections", type_="foreignkey")
    for column in (
        "source_snapshot_locked", "follow_up_of_id", "rejected_at", "confirmed_at",
        "submitted_at", "reassignment_reason", "review_reason", "reviewed_at", "reviewed_by_id",
        "updated_by_id", "due_at", "priority", "source_reason", "source_zone", "source_point",
        "source_geometry_hash", "source_delta", "source_comparison_value", "source_sampled_value",
        "source_index_name", "source_acquired_at", "source_item_id", "source_provider",
        "source_alert_id", "source_kind",
    ):
        op.drop_column("field_inspections", column)
    op.create_check_constraint(
        "ck_field_inspections_status", "field_inspections",
        "status IN ('pending','in_progress','completed','cancelled')",
    )
    op.create_check_constraint(
        "ck_field_inspections_source", "field_inspections",
        "source IN ('attention_queue','manual','irrigation_context')",
    )
    op.create_check_constraint(
        "ck_field_inspections_source_priority", "field_inspections",
        "source_priority IS NULL OR source_priority IN ('low','medium','high','critical')",
    )
    op.create_check_constraint(
        "ck_field_inspections_terminal_timestamps", "field_inspections",
        "(status = 'completed' AND completed_at IS NOT NULL AND cancelled_at IS NULL) OR "
        "(status = 'cancelled' AND cancelled_at IS NOT NULL AND completed_at IS NULL) OR "
        "(status IN ('pending','in_progress') AND completed_at IS NULL AND cancelled_at IS NULL)",
    )
    op.create_index(
        "uq_field_inspections_one_active_per_field", "field_inspections", ["field_id"],
        unique=True, postgresql_where=sa.text("status IN ('pending','in_progress')"),
    )
