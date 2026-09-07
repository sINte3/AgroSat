"""Explicit tenant-bound metadata; no ORM relationships or implicit SQL."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from geoalchemy2 import Geometry
from database import Base

PLAN_STATES = "'draft','approved','in_progress','pending_verification','rework','closed','cancelled','superseded'"
VERIFICATION_STATES = "'PENDING_DATA','TOO_EARLY','CLOUD_BLOCKED','QUALITY_BLOCKED','PROVIDER_DEGRADED','INCONCLUSIVE','IMPROVED','NO_MATERIAL_CHANGE','WORSENED'"
NEW_TABLES = ("agronomy_plans", "agronomy_work_items", "agronomy_verifications", "agronomy_events")


def register_closed_loop_agronomy():
    m = Base.metadata
    if "agronomy_plans" in m.tables:
        return

    def col(name, kind=sa.Integer(), *constraints, **kwargs):
        return sa.Column(name, kind, *constraints, **kwargs)

    def fk(local, remote, name):
        return sa.ForeignKeyConstraint(local.split(","), remote.split(","), name=name, ondelete="RESTRICT")

    def identity():
        return [col("id", sa.BigInteger(), primary_key=True), col("enterprise_id", nullable=False), col("field_id", nullable=False)]

    def version():
        return col("version", nullable=False, server_default="1")

    def time(name, required=False):
        return col(name, sa.DateTime(timezone=True), nullable=not required, **({"server_default": sa.text("now()")} if required else {}))

    def check(expr, name):
        return sa.CheckConstraint(expr, name=name)

    for table, columns, name in (
        ("ndvi_records", ["id", "field_id"], "uq_ndvi_id_field"),
        ("alerts", ["id", "field_id"], "uq_alert_id_field"),
        ("autonomous_anomaly_candidates", ["id", "inspection_id", "field_id", "enterprise_id"], "uq_candidate_case_binding"),
    ):
        m.tables[table].append_constraint(sa.UniqueConstraint(*columns, name=name))
    candidate = m.tables["autonomous_anomaly_candidates"]
    for constraint in list(candidate.constraints):
        if constraint.name == "ck_anomaly_candidates_inspection_state":
            candidate.constraints.remove(constraint)
    candidate.append_constraint(check(
        "(state<>'INSPECTION_CREATED' OR inspection_id IS NOT NULL) AND "
        "(inspection_id IS NULL OR state IN ('INSPECTION_CREATED','RESOLVED','SUPERSEDED'))",
        "ck_anomaly_candidates_inspection_state"))

    plan = sa.Table("agronomy_plans", m, *identity(),
        col("inspection_id", nullable=False), col("candidate_id", sa.BigInteger()), col("alert_id"),
        col("supersedes_id", sa.BigInteger(), sa.ForeignKey("agronomy_plans.id", ondelete="RESTRICT")),
        col("decision", sa.Text(), nullable=False), col("objective", sa.Text(), nullable=False),
        col("expected_outcome", sa.Text(), nullable=False), col("priority", sa.String(16), nullable=False),
        col("policy_version", sa.String(40), nullable=False), col("input_snapshot", JSONB(), nullable=False),
        col("recommendation", JSONB(), nullable=False), col("status", sa.String(24), nullable=False), version(),
        col("baseline_record_id"), col("baseline", JSONB()),
        col("verification_status", sa.String(24), nullable=False, server_default="PENDING_DATA"),
        col("cycle", nullable=False, server_default="1"),
        col("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        col("approved_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        col("started_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        col("completed_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        col("closed_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        time("created_at", True), time("approved_at"), time("started_at"), time("completed_at"), time("closed_at"),
        col("resolution_reason", sa.Text()),
        fk("enterprise_id", "enterprises.id", "fk_agronomy_plan_enterprise"),
        fk("inspection_id,field_id,enterprise_id", "field_inspections.id,field_inspections.field_id,field_inspections.enterprise_id", "fk_agronomy_plan_inspection"),
        fk("field_id,enterprise_id", "fields.id,fields.enterprise_id", "fk_agronomy_plan_field"),
        fk("candidate_id,inspection_id,field_id,enterprise_id", "autonomous_anomaly_candidates.id,autonomous_anomaly_candidates.inspection_id,autonomous_anomaly_candidates.field_id,autonomous_anomaly_candidates.enterprise_id", "fk_agronomy_plan_candidate"),
        fk("alert_id,field_id", "alerts.id,alerts.field_id", "fk_agronomy_plan_alert"),
        fk("baseline_record_id,field_id", "ndvi_records.id,ndvi_records.field_id", "fk_agronomy_plan_baseline"),
        sa.UniqueConstraint("id", "inspection_id", "field_id", "enterprise_id", name="uq_agronomy_plan_binding"),
        check(f"status IN ({PLAN_STATES}) AND version>=1 AND cycle>=1", "ck_agronomy_plan_state"),
        check("priority IN ('low','normal','high','urgent') AND policy_version='r3-f-v1'", "ck_agronomy_plan_policy"),
        check("length(btrim(decision)) BETWEEN 5 AND 4000 AND length(btrim(objective)) BETWEEN 5 AND 4000 AND length(btrim(expected_outcome)) BETWEEN 5 AND 4000", "ck_agronomy_plan_text"),
        check("status IN ('draft','cancelled','superseded') OR (approved_at IS NOT NULL AND approved_by_id IS NOT NULL)", "ck_agronomy_plan_approval"),
        check("status NOT IN ('pending_verification','closed') OR completed_at IS NOT NULL", "ck_agronomy_plan_completion"),
        check("status NOT IN ('closed','cancelled','superseded') OR length(btrim(resolution_reason))>=5", "ck_agronomy_plan_reason"),
        check("status<>'closed' OR (closed_at IS NOT NULL AND closed_by_id IS NOT NULL)", "ck_agronomy_plan_closed"),
        check(f"verification_status IN ({VERIFICATION_STATES})", "ck_agronomy_plan_verification"),
    )
    sa.Index("uq_agronomy_plan_active_inspection", plan.c.inspection_id, unique=True,
             postgresql_where=sa.text("status NOT IN ('closed','cancelled','superseded')"))
    sa.Index("ix_agronomy_plan_queue", plan.c.enterprise_id, plan.c.status, plan.c.priority, plan.c.created_at)
    sa.Index("ix_agronomy_plan_verification", plan.c.status, plan.c.completed_at)

    item = sa.Table("agronomy_work_items", m, *identity(),
        col("plan_id", sa.BigInteger(), nullable=False), col("inspection_id", nullable=False),
        col("cycle", nullable=False), col("category", sa.String(32), nullable=False),
        col("instruction", sa.Text(), nullable=False), col("assigned_to_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        time("planned_start_at"), time("due_at"),
        col("geometry", Geometry("GEOMETRY", srid=4326, spatial_index=False)),
        col("status", sa.String(24), nullable=False, server_default="planned"), version(),
        time("started_at"), time("completed_at"), col("result_note", sa.Text()), time("created_at", True),
        fk("plan_id,inspection_id,field_id,enterprise_id", "agronomy_plans.id,agronomy_plans.inspection_id,agronomy_plans.field_id,agronomy_plans.enterprise_id", "fk_agronomy_work_plan"),
        sa.UniqueConstraint("id", "inspection_id", "field_id", "enterprise_id", name="uq_agronomy_work_binding"),
        check("category IN ('irrigation','nutrition','crop_protection','drainage','reinspection','sampling','cultivation','other')", "ck_agronomy_work_category"),
        check("length(btrim(instruction)) BETWEEN 5 AND 4000 AND version>=1 AND cycle>=1", "ck_agronomy_work_content"),
        check("status IN ('planned','in_progress','completed','cancelled')", "ck_agronomy_work_status"),
        check("status NOT IN ('in_progress','completed') OR (assigned_to_id IS NOT NULL AND started_at IS NOT NULL AND due_at IS NOT NULL)", "ck_agronomy_work_started"),
        check("status<>'completed' OR (completed_at>=started_at AND length(btrim(result_note))>=5)", "ck_agronomy_work_completed"),
        check("planned_start_at IS NULL OR due_at>=planned_start_at", "ck_agronomy_work_dates"),
        check("geometry IS NULL OR (ST_SRID(geometry)=4326 AND GeometryType(geometry) IN ('POINT','POLYGON','MULTIPOLYGON') AND ST_IsValid(geometry) AND NOT ST_IsEmpty(geometry))", "ck_agronomy_work_geometry"),
    )
    sa.Index("ix_agronomy_work_assignment", item.c.enterprise_id, item.c.assigned_to_id, item.c.status, item.c.due_at)
    sa.Index("ix_agronomy_work_plan", item.c.plan_id, item.c.cycle)
    sa.Index("ix_agronomy_work_geometry", item.c.geometry, postgresql_using="gist")

    verification = sa.Table("agronomy_verifications", m, *identity(),
        col("plan_id", sa.BigInteger(), nullable=False), col("inspection_id", nullable=False),
        col("cycle", nullable=False), col("baseline_record_id"), col("post_record_id"),
        col("evaluation_key", sa.String(64), nullable=False), col("policy_version", sa.String(40), nullable=False),
        col("status", sa.String(24), nullable=False), col("measurements", JSONB(), nullable=False),
        time("completed_at"), col("post_date", sa.Date()), time("created_at", True),
        fk("plan_id,inspection_id,field_id,enterprise_id", "agronomy_plans.id,agronomy_plans.inspection_id,agronomy_plans.field_id,agronomy_plans.enterprise_id", "fk_agronomy_verification_plan"),
        fk("baseline_record_id,field_id", "ndvi_records.id,ndvi_records.field_id", "fk_agronomy_verification_baseline"),
        fk("post_record_id,field_id", "ndvi_records.id,ndvi_records.field_id", "fk_agronomy_verification_post"),
        sa.UniqueConstraint("plan_id", "cycle", "evaluation_key", "policy_version", name="uq_agronomy_verification_identity"),
        check(f"status IN ({VERIFICATION_STATES}) AND policy_version='r3-f-v1' AND cycle>=1", "ck_agronomy_verification_policy"),
        check("post_record_id IS NULL OR (completed_at IS NOT NULL AND post_date > (completed_at AT TIME ZONE 'UTC')::date + 7)", "ck_agronomy_verification_temporal"),
        check("status NOT IN ('IMPROVED','NO_MATERIAL_CHANGE','WORSENED') OR (baseline_record_id IS NOT NULL AND post_record_id IS NOT NULL)", "ck_agronomy_verification_result"),
    )
    sa.Index("ix_agronomy_verification_history", verification.c.enterprise_id, verification.c.plan_id, verification.c.id)

    event = sa.Table("agronomy_events", m, *identity(),
        col("plan_id", sa.BigInteger(), nullable=False), col("inspection_id", nullable=False),
        col("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        col("actor_key", sa.String(40), nullable=False), col("command_key", sa.String(64), nullable=False),
        col("fingerprint", sa.String(64), nullable=False), col("event_type", sa.String(40), nullable=False),
        col("version", nullable=False), col("detail", JSONB(), nullable=False), col("response", JSONB(), nullable=False),
        time("occurred_at", True),
        fk("plan_id,inspection_id,field_id,enterprise_id", "agronomy_plans.id,agronomy_plans.inspection_id,agronomy_plans.field_id,agronomy_plans.enterprise_id", "fk_agronomy_event_plan"),
        sa.UniqueConstraint("actor_key", "command_key", name="uq_agronomy_event_command"),
        check("version>=1 AND fingerprint ~ '^[a-f0-9]{64}$'", "ck_agronomy_event_identity"),
        check("(actor_id IS NULL AND actor_key='collector') OR actor_key='user:'||actor_id::text", "ck_agronomy_event_actor"),
    )
    sa.Index("ix_agronomy_event_timeline", event.c.enterprise_id, event.c.plan_id, event.c.id)
    evidence = m.tables["inspection_evidence"]
    evidence.append_column(col("agronomy_work_item_id", sa.BigInteger()))
    evidence.append_constraint(fk("agronomy_work_item_id,inspection_id,field_id,enterprise_id", "agronomy_work_items.id,agronomy_work_items.inspection_id,agronomy_work_items.field_id,agronomy_work_items.enterprise_id", "fk_inspection_evidence_agronomy_work"))
    sa.Index("ix_inspection_evidence_agronomy_work", evidence.c.agronomy_work_item_id)


register_closed_loop_agronomy()
