"""Add the autonomous satellite monitoring domain.

Revision ID: 0014_autonomous_satellite_monitoring
Revises: 0013_anomaly_inspection_workflow
"""

from typing import Sequence, Union

from alembic import op
import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0014_autonomous_satellite_monitoring"
down_revision: Union[str, Sequence[str], None] = "0013_anomaly_inspection_workflow"
branch_labels = None
depends_on = None


RULE_VERSION = "r3-e-v1"
RULE_HASH = "4434900a8905885c67dfe8eb2717fffc17b0f2444aae14051c194cba8cc2da8d"


def upgrade() -> None:
    op.create_table(
        "monitoring_rule_versions",
        sa.Column("version", sa.String(40), primary_key=True),
        sa.Column("algorithm", sa.String(40), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version ~ '^[a-z0-9][a-z0-9._-]{2,39}$'", name="ck_monitoring_rule_versions_version"),
        sa.CheckConstraint("algorithm = 'rolling_median_mad'", name="ck_monitoring_rule_versions_algorithm"),
        sa.CheckConstraint("configuration_hash ~ '^[0-9a-f]{64}$'", name="ck_monitoring_rule_versions_hash"),
        sa.CheckConstraint("jsonb_typeof(configuration) = 'object'", name="ck_monitoring_rule_versions_config"),
        sa.CheckConstraint("NOT is_active OR retired_at IS NULL", name="ck_monitoring_rule_versions_active"),
    )
    op.create_index(
        "uq_monitoring_rule_versions_one_active",
        "monitoring_rule_versions",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO monitoring_rule_versions
              (version, algorithm, configuration, configuration_hash, is_active)
            VALUES
              (:version, 'rolling_median_mad', CAST(:configuration AS jsonb), :hash, true)
            """
        ).bindparams(
            version=RULE_VERSION,
            hash=RULE_HASH,
            configuration=(
                '{"auto_inspection_cap":20,"cooldown_days":14,'
                '"extreme_deviation":6.0,"fresh_days":10,"high_confidence":0.85,'
                '"max_spike_fraction":0.1,"min_area_ha":0.25,'
                '"min_area_fraction":0.01,"min_baseline_scenes":5,'
                '"min_persistence_scenes":2,"moderate_confidence":0.6,'
                '"stale_days":20,"supporting_index_agreement":2}'
            ),
        )
    )

    op.create_table(
        "satellite_collection_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_key", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("release_commit", sa.String(40), nullable=False),
        sa.Column("rule_version", sa.String(40), nullable=False),
        sa.Column("audit_identity", sa.String(255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_status", sa.String(32), nullable=False),
        sa.Column("failure_category", sa.String(32), nullable=True),
        sa.Column("retry_state", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("counters", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["rule_version"], ["monitoring_rule_versions.version"], name="fk_collection_runs_rule", ondelete="RESTRICT"),
        sa.UniqueConstraint("run_key", name="uq_satellite_collection_runs_run_key"),
        sa.CheckConstraint("mode IN ('dry-run','diagnostic','apply','idempotency')", name="ck_collection_runs_mode"),
        sa.CheckConstraint("status IN ('running','succeeded','degraded','failed','cancelled','lock_contended')", name="ck_collection_runs_status"),
        sa.CheckConstraint("release_commit ~ '^[0-9a-f]{40}$'", name="ck_collection_runs_release"),
        sa.CheckConstraint("provider_status IN ('pending','healthy','no_scene','cloud_blocked','quality_blocked','degraded','not_called')", name="ck_collection_runs_provider"),
        sa.CheckConstraint("failure_category IS NULL OR failure_category IN ('auth','quota','network','timeout','quality','lock_contention','contract','operational')", name="ck_collection_runs_failure"),
        sa.CheckConstraint("heartbeat_at >= started_at AND (finished_at IS NULL OR finished_at >= started_at)", name="ck_collection_runs_times"),
        sa.CheckConstraint("jsonb_typeof(retry_state)='object' AND jsonb_typeof(counters)='object'", name="ck_collection_runs_json"),
    )
    op.create_index("ix_collection_runs_status_started", "satellite_collection_runs", ["status", sa.text("started_at DESC")])
    op.create_index("ix_collection_runs_heartbeat", "satellite_collection_runs", ["heartbeat_at"], postgresql_where=sa.text("status='running'"))

    op.create_table(
        "satellite_field_freshness",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("index_code", sa.String(8), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("last_attempted_scene", sa.String(768), nullable=True),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_accepted_scene", sa.String(768), nullable=True),
        sa.Column("last_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_reason", sa.String(64), nullable=True),
        sa.Column("last_quality_reason", sa.String(64), nullable=True),
        sa.Column("next_eligible_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], name="fk_field_freshness_enterprise", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["field_id"], ["fields.id"], name="fk_field_freshness_field", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_run_id"], ["satellite_collection_runs.id"], name="fk_field_freshness_run", ondelete="SET NULL"),
        sa.UniqueConstraint("field_id", "index_code", name="uq_field_freshness_field_index"),
        sa.CheckConstraint("index_code IN ('ndvi','savi','evi','ndmi','ndre')", name="ck_field_freshness_index"),
        sa.CheckConstraint("status IN ('FRESH','AGING','STALE','NEVER_COLLECTED','CLOUD_BLOCKED','PROVIDER_DEGRADED','QUALITY_BLOCKED')", name="ck_field_freshness_status"),
        sa.CheckConstraint("last_accepted_at IS NOT NULL OR last_accepted_scene IS NULL", name="ck_field_freshness_accepted"),
    )
    op.create_index("ix_field_freshness_enterprise_status", "satellite_field_freshness", ["enterprise_id", "status", "index_code"])
    op.create_index("ix_field_freshness_next_eligible", "satellite_field_freshness", ["next_eligible_at"], postgresql_where=sa.text("next_eligible_at IS NOT NULL"))

    op.create_table(
        "autonomous_anomaly_candidates",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_version", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("scene_id", sa.String(768), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("index_code", sa.String(8), nullable=False),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("zone_key", sa.String(64), nullable=False),
        sa.Column("geometry", geoalchemy2.Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("magnitude", sa.Float(), nullable=False),
        sa.Column("robust_deviation", sa.Float(), nullable=False),
        sa.Column("affected_area_ha", sa.Float(), nullable=False),
        sa.Column("affected_area_fraction", sa.Float(), nullable=False),
        sa.Column("persistence_scenes", sa.Integer(), nullable=False),
        sa.Column("multi_index_agreement", sa.Integer(), nullable=False),
        sa.Column("data_quality", sa.Float(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("inspection_id", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], name="fk_anomaly_candidates_enterprise", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["field_id"], ["fields.id"], name="fk_anomaly_candidates_field", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["satellite_collection_runs.id"], name="fk_anomaly_candidates_run", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rule_version"], ["monitoring_rule_versions.version"], name="fk_anomaly_candidates_rule", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["inspection_id"], ["field_inspections.id"], name="fk_anomaly_candidates_inspection", ondelete="RESTRICT"),
        sa.UniqueConstraint("enterprise_id", "field_id", "index_code", "scene_id", "zone_key", "rule_version", name="uq_anomaly_candidates_scene_zone"),
        sa.CheckConstraint("index_code IN ('ndvi','savi','evi','ndmi','ndre')", name="ck_anomaly_candidates_index"),
        sa.CheckConstraint("source_key ~ '^[0-9a-f]{64}$' AND zone_key ~ '^[0-9a-f]{64}$'", name="ck_anomaly_candidates_keys"),
        sa.CheckConstraint("score BETWEEN 0 AND 1 AND confidence BETWEEN 0 AND 1 AND data_quality BETWEEN 0 AND 1", name="ck_anomaly_candidates_scores"),
        sa.CheckConstraint("severity IN ('LOW','MODERATE','HIGH','EXTREME')", name="ck_anomaly_candidates_severity"),
        sa.CheckConstraint("affected_area_ha >= 0.25 AND affected_area_fraction BETWEEN 0.01 AND 1", name="ck_anomaly_candidates_area"),
        sa.CheckConstraint("persistence_scenes >= 1 AND multi_index_agreement BETWEEN 0 AND 4", name="ck_anomaly_candidates_evidence_counts"),
        sa.CheckConstraint("state IN ('NEW','CONFIRMED','DISMISSED','INSPECTION_CREATED','RESOLVED','SUPERSEDED')", name="ck_anomaly_candidates_state"),
        sa.CheckConstraint("version >= 1 AND jsonb_typeof(evidence)='object'", name="ck_anomaly_candidates_version_evidence"),
        sa.CheckConstraint("ST_SRID(geometry)=4326 AND GeometryType(geometry)='MULTIPOLYGON' AND NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry)", name="ck_anomaly_candidates_geometry"),
        sa.CheckConstraint("(state='INSPECTION_CREATED') = (inspection_id IS NOT NULL)", name="ck_anomaly_candidates_inspection_state"),
    )
    op.create_index("ix_anomaly_candidates_enterprise_state", "autonomous_anomaly_candidates", ["enterprise_id", "state", sa.text("created_at DESC")])
    op.create_index("ix_anomaly_candidates_field_acquired", "autonomous_anomaly_candidates", ["field_id", sa.text("acquired_at DESC")])
    op.create_index("ix_anomaly_candidates_geometry", "autonomous_anomaly_candidates", ["geometry"], postgresql_using="gist")
    op.create_index("ix_anomaly_candidates_cooldown", "autonomous_anomaly_candidates", ["enterprise_id", "source_key", "zone_key", "cooldown_until"])
    op.create_index("uq_anomaly_candidates_inspection", "autonomous_anomaly_candidates", ["inspection_id"], unique=True, postgresql_where=sa.text("inspection_id IS NOT NULL"))

    op.create_table(
        "autonomous_anomaly_transitions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(24), nullable=True),
        sa.Column("to_state", sa.String(24), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["candidate_id"], ["autonomous_anomaly_candidates.id"], name="fk_anomaly_transitions_candidate", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], name="fk_anomaly_transitions_enterprise", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], name="fk_anomaly_transitions_actor", ondelete="RESTRICT"),
        sa.CheckConstraint("from_state IS NULL OR from_state IN ('NEW','CONFIRMED','DISMISSED','INSPECTION_CREATED','RESOLVED','SUPERSEDED')", name="ck_anomaly_transitions_from"),
        sa.CheckConstraint("to_state IN ('NEW','CONFIRMED','DISMISSED','INSPECTION_CREATED','RESOLVED','SUPERSEDED')", name="ck_anomaly_transitions_to"),
        sa.CheckConstraint("length(btrim(reason)) BETWEEN 3 AND 2000 AND expected_version >= 1", name="ck_anomaly_transitions_reason"),
    )
    op.create_index("ix_anomaly_transitions_candidate", "autonomous_anomaly_transitions", ["candidate_id", "id"])
    op.create_index("ix_anomaly_transitions_enterprise_created", "autonomous_anomaly_transitions", ["enterprise_id", sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("ix_anomaly_transitions_enterprise_created", table_name="autonomous_anomaly_transitions")
    op.drop_index("ix_anomaly_transitions_candidate", table_name="autonomous_anomaly_transitions")
    op.drop_table("autonomous_anomaly_transitions")
    op.drop_index("uq_anomaly_candidates_inspection", table_name="autonomous_anomaly_candidates")
    op.drop_index("ix_anomaly_candidates_cooldown", table_name="autonomous_anomaly_candidates")
    op.drop_index("ix_anomaly_candidates_geometry", table_name="autonomous_anomaly_candidates")
    op.drop_index("ix_anomaly_candidates_field_acquired", table_name="autonomous_anomaly_candidates")
    op.drop_index("ix_anomaly_candidates_enterprise_state", table_name="autonomous_anomaly_candidates")
    op.drop_table("autonomous_anomaly_candidates")
    op.drop_index("ix_field_freshness_next_eligible", table_name="satellite_field_freshness")
    op.drop_index("ix_field_freshness_enterprise_status", table_name="satellite_field_freshness")
    op.drop_table("satellite_field_freshness")
    op.drop_index("ix_collection_runs_heartbeat", table_name="satellite_collection_runs")
    op.drop_index("ix_collection_runs_status_started", table_name="satellite_collection_runs")
    op.drop_table("satellite_collection_runs")
    op.drop_index("uq_monitoring_rule_versions_one_active", table_name="monitoring_rule_versions")
    op.drop_table("monitoring_rule_versions")
