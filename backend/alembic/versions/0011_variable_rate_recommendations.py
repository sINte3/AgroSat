"""Add human-entered variable-rate recommendation drafts.

Revision ID: 0011_variable_rate_recommendations
Revises: 0010_productivity_zones
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0011_variable_rate_recommendations"
down_revision: Union[str, Sequence[str], None] = "0010_productivity_zones"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "variable_rate_recommendations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("productivity_run_id", sa.Integer(), nullable=False),
        sa.Column("parent_recommendation_id", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("crop_code", sa.String(64), nullable=False),
        sa.Column("season_year", sa.Integer(), nullable=False),
        sa.Column("recommendation_kind", sa.String(24), nullable=False),
        sa.Column("rate_unit", sa.String(20), nullable=False),
        sa.Column("minimum_rate", sa.Numeric(14, 4), nullable=False),
        sa.Column("maximum_rate", sa.Numeric(14, 4), nullable=False),
        sa.Column("zone_rates", postgresql.JSONB(), nullable=False),
        sa.Column("equipment_capability", postgresql.JSONB(), nullable=False),
        sa.Column("source_confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("source_unzoned_area_ha", sa.Numeric(14, 4), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("safety_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("approved_by_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by_id", sa.Integer(), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("client_request_id", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
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
            ["enterprise_id"],
            ["enterprises.id"],
            name="fk_variable_rate_recommendations_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["field_id", "enterprise_id"],
            ["fields.id", "fields.enterprise_id"],
            name="fk_variable_rate_recommendations_field_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["productivity_run_id", "enterprise_id", "field_id"],
            [
                "productivity_zone_runs.id",
                "productivity_zone_runs.enterprise_id",
                "productivity_zone_runs.field_id",
            ],
            name="fk_variable_rate_recommendations_run_enterprise_field",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_variable_rate_recommendations_creator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_id"],
            ["users.id"],
            name="fk_variable_rate_recommendations_approver",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rejected_by_id"],
            ["users.id"],
            name="fk_variable_rate_recommendations_rejector",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "enterprise_id",
            "field_id",
            name="uq_variable_rate_recommendations_id_enterprise_field",
        ),
        sa.UniqueConstraint(
            "enterprise_id",
            "created_by_id",
            "client_request_id",
            name="uq_variable_rate_recommendations_actor_request",
        ),
        sa.UniqueConstraint(
            "enterprise_id",
            "field_id",
            "recommendation_kind",
            "season_year",
            "version",
            name="uq_variable_rate_recommendations_version",
        ),
        sa.CheckConstraint(
            "version > 0 AND season_year BETWEEN 2000 AND 2200",
            name="ck_variable_rate_recommendations_version_season",
        ),
        sa.CheckConstraint(
            "(recommendation_kind='fertilizer' AND rate_unit='kg_ha') OR "
            "(recommendation_kind='seed' AND rate_unit='seeds_ha') OR "
            "(recommendation_kind='pesticide' AND rate_unit='l_ha') OR "
            "(recommendation_kind='irrigation' AND rate_unit='mm')",
            name="ck_variable_rate_recommendations_kind_unit",
        ),
        sa.CheckConstraint(
            "minimum_rate >= 0 AND maximum_rate >= minimum_rate",
            name="ck_variable_rate_recommendations_bounds",
        ),
        sa.CheckConstraint(
            "source_confidence BETWEEN 0 AND 1 "
            "AND source_unzoned_area_ha >= 0",
            name="ck_variable_rate_recommendations_source_quality",
        ),
        sa.CheckConstraint(
            "safety_acknowledged = true",
            name="ck_variable_rate_recommendations_safety_ack",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(zone_rates)='object' "
            "AND zone_rates ?& ARRAY['low','medium','high']::text[] "
            "AND (zone_rates - ARRAY['low','medium','high']::text[]) = '{}'::jsonb "
            "AND jsonb_typeof(equipment_capability)='object'",
            name="ck_variable_rate_recommendations_json_shapes",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_variable_rate_recommendations_fingerprint",
        ),
        sa.CheckConstraint(
            "status IN ('draft','approved','rejected','superseded')",
            name="ck_variable_rate_recommendations_status",
        ),
        sa.CheckConstraint(
            "(status='draft' AND approved_by_id IS NULL AND approved_at IS NULL "
            "AND rejected_by_id IS NULL AND rejected_at IS NULL) OR "
            "(status='approved' AND approved_by_id IS NOT NULL "
            "AND approved_at IS NOT NULL AND rejected_by_id IS NULL "
            "AND rejected_at IS NULL AND decision_note IS NOT NULL) OR "
            "(status='rejected' AND rejected_by_id IS NOT NULL "
            "AND rejected_at IS NOT NULL AND approved_by_id IS NULL "
            "AND approved_at IS NULL AND decision_note IS NOT NULL) OR "
            "(status='superseded')",
            name="ck_variable_rate_recommendations_decision",
        ),
    )
    op.create_foreign_key(
        "fk_variable_rate_recommendations_parent_enterprise_field",
        "variable_rate_recommendations",
        "variable_rate_recommendations",
        ["parent_recommendation_id", "enterprise_id", "field_id"],
        ["id", "enterprise_id", "field_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_variable_rate_recommendations_enterprise_field_status",
        "variable_rate_recommendations",
        ["enterprise_id", "field_id", "status", sa.text("created_at DESC")],
    )

    op.create_table(
        "variable_rate_recommendation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recommendation_id", sa.Integer(), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_id", "enterprise_id", "field_id"],
            [
                "variable_rate_recommendations.id",
                "variable_rate_recommendations.enterprise_id",
                "variable_rate_recommendations.field_id",
            ],
            name="fk_variable_rate_events_recommendation_enterprise_field",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_variable_rate_events_actor",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "event_type IN ('created','approved','rejected','superseded')",
            name="ck_variable_rate_events_type",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details)='object'",
            name="ck_variable_rate_events_details",
        ),
    )
    op.create_index(
        "ix_variable_rate_events_recommendation_created",
        "variable_rate_recommendation_events",
        ["recommendation_id", sa.text("created_at ASC"), "id"],
    )


def downgrade() -> None:
    context = op.get_context()
    if not context.as_sql:
        count = op.get_bind().execute(
            sa.text("SELECT count(*) FROM variable_rate_recommendations")
        ).scalar_one()
        if count:
            raise RuntimeError(
                "Cannot downgrade while variable-rate recommendations exist"
            )
    op.drop_index(
        "ix_variable_rate_events_recommendation_created",
        table_name="variable_rate_recommendation_events",
    )
    op.drop_table("variable_rate_recommendation_events")
    op.drop_index(
        "ix_variable_rate_recommendations_enterprise_field_status",
        table_name="variable_rate_recommendations",
    )
    op.drop_constraint(
        "fk_variable_rate_recommendations_parent_enterprise_field",
        "variable_rate_recommendations",
        type_="foreignkey",
    )
    op.drop_table("variable_rate_recommendations")
