"""Add commercial tenant configuration and reviewed lifecycle requests.

Revision ID: 0012_commercial_tenant_boundary
Revises: 0011_variable_rate_recommendations
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0012_commercial_tenant_boundary"
down_revision: Union[str, Sequence[str], None] = (
    "0011_variable_rate_recommendations"
)
branch_labels = None
depends_on = None

MEMBERSHIP_ROLES = ("owner", "manager", "agronomist", "viewer")
REQUEST_TYPES = ("export", "deletion")
REQUEST_STATUSES = (
    "requested",
    "approved",
    "rejected",
    "executing",
    "completed",
    "failed",
)


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def upgrade() -> None:
    op.create_table(
        "enterprise_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("membership_role", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["enterprise_id"],
            ["enterprises.id"],
            name="fk_enterprise_memberships_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_enterprise_memberships_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_enterprise_memberships_creator",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "enterprise_id",
            "user_id",
            name="uq_enterprise_memberships_enterprise_user",
        ),
        sa.CheckConstraint(
            "membership_role IN ('owner','manager','agronomist','viewer')",
            name="ck_enterprise_memberships_role",
        ),
        sa.CheckConstraint(
            "status IN ('invited','active','suspended','revoked')",
            name="ck_enterprise_memberships_status",
        ),
    )
    op.create_index(
        "ix_enterprise_memberships_enterprise_status",
        "enterprise_memberships",
        ["enterprise_id", "status", "membership_role"],
    )

    op.create_table(
        "enterprise_commercial_profiles",
        sa.Column("enterprise_id", sa.Integer(), primary_key=True),
        sa.Column("plan_code", sa.String(64), nullable=False),
        sa.Column("subscription_state", sa.String(24), nullable=False),
        sa.Column("feature_flags", postgresql.JSONB(), nullable=False),
        sa.Column("quota_limits", postgresql.JSONB(), nullable=False),
        sa.Column("retention_policy", postgresql.JSONB(), nullable=False),
        sa.Column("branding", postgresql.JSONB(), nullable=False),
        sa.Column("namespaces", postgresql.JSONB(), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["enterprise_id"],
            ["enterprises.id"],
            name="fk_enterprise_commercial_profiles_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name="fk_enterprise_commercial_profiles_updater",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "subscription_state IN "
            "('unsupported','trial','active','suspended','terminated')",
            name="ck_enterprise_commercial_profiles_subscription",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(feature_flags)='object' "
            "AND jsonb_typeof(quota_limits)='object' "
            "AND jsonb_typeof(retention_policy)='object' "
            "AND jsonb_typeof(branding)='object' "
            "AND jsonb_typeof(namespaces)='object'",
            name="ck_enterprise_commercial_profiles_json",
        ),
    )
    op.create_index(
        "ix_enterprise_commercial_profiles_enterprise",
        "enterprise_commercial_profiles",
        ["enterprise_id"],
    )

    op.create_table(
        "tenant_provider_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("provider_code", sa.String(40), nullable=False),
        sa.Column("secret_reference", sa.String(255), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_category", sa.String(40), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["enterprise_id"],
            ["enterprises.id"],
            name="fk_tenant_provider_credentials_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name="fk_tenant_provider_credentials_updater",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "enterprise_id",
            "provider_code",
            name="uq_tenant_provider_credentials_enterprise_provider",
        ),
        sa.CheckConstraint(
            "provider_code ~ '^[a-z][a-z0-9_]{1,39}$'",
            name="ck_tenant_provider_credentials_provider",
        ),
        sa.CheckConstraint(
            "secret_reference ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{2,254}$'",
            name="ck_tenant_provider_credentials_reference",
        ),
        sa.CheckConstraint(
            "status IN ('unconfigured','configured','valid','invalid','disabled')",
            name="ck_tenant_provider_credentials_status",
        ),
        sa.CheckConstraint(
            "last_failure_category IS NULL OR last_failure_category IN "
            "('auth','quota','network','timeout','mapping','unknown')",
            name="ck_tenant_provider_credentials_failure",
        ),
    )
    op.create_index(
        "ix_tenant_provider_credentials_enterprise_status",
        "tenant_provider_credentials",
        ["enterprise_id", "status", "provider_code"],
    )

    op.create_table(
        "tenant_lifecycle_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("request_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requested_by_id", sa.Integer(), nullable=False),
        sa.Column("reviewed_by_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("client_request_id", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("result_reference", sa.String(255), nullable=True),
        sa.Column("failure_category", sa.String(40), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["enterprise_id"],
            ["enterprises.id"],
            name="fk_tenant_lifecycle_requests_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name="fk_tenant_lifecycle_requests_requester",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"],
            ["users.id"],
            name="fk_tenant_lifecycle_requests_reviewer",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "enterprise_id",
            "requested_by_id",
            "client_request_id",
            name="uq_tenant_lifecycle_requests_actor_request",
        ),
        sa.CheckConstraint(
            "request_type IN ('export','deletion')",
            name="ck_tenant_lifecycle_requests_type",
        ),
        sa.CheckConstraint(
            "status IN "
            "('requested','approved','rejected','executing','completed','failed')",
            name="ck_tenant_lifecycle_requests_status",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) BETWEEN 10 AND 2000",
            name="ck_tenant_lifecycle_requests_reason",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_tenant_lifecycle_requests_fingerprint",
        ),
        sa.CheckConstraint(
            "(status='requested' AND reviewed_by_id IS NULL "
            "AND reviewed_at IS NULL) OR "
            "(status IN ('approved','rejected') AND reviewed_by_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND decision_note IS NOT NULL) OR "
            "(status IN ('executing','completed','failed') "
            "AND reviewed_by_id IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_tenant_lifecycle_requests_review",
        ),
    )
    op.create_index(
        "ix_tenant_lifecycle_requests_enterprise_status",
        "tenant_lifecycle_requests",
        ["enterprise_id", "status", "request_type", sa.text("created_at DESC")],
    )

    op.create_table(
        "tenant_commercial_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(40), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id"],
            ["enterprises.id"],
            name="fk_tenant_commercial_audit_events_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_tenant_commercial_audit_events_actor",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "action ~ '^[a-z][a-z0-9_.-]{2,63}$' "
            "AND target_type ~ '^[a-z][a-z0-9_]{1,39}$'",
            name="ck_tenant_commercial_audit_events_names",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details)='object'",
            name="ck_tenant_commercial_audit_events_details",
        ),
    )
    op.create_index(
        "ix_tenant_commercial_audit_events_enterprise_created",
        "tenant_commercial_audit_events",
        ["enterprise_id", sa.text("created_at DESC"), "id"],
    )


def downgrade() -> None:
    context = op.get_context()
    if not context.as_sql:
        for table in (
            "enterprise_memberships",
            "enterprise_commercial_profiles",
            "tenant_provider_credentials",
            "tenant_lifecycle_requests",
            "tenant_commercial_audit_events",
        ):
            count = op.get_bind().execute(
                sa.text(f"SELECT count(*) FROM {table}")
            ).scalar_one()
            if count:
                raise RuntimeError(
                    "Cannot downgrade while commercial tenant data exists"
                )
    op.drop_index(
        "ix_tenant_commercial_audit_events_enterprise_created",
        table_name="tenant_commercial_audit_events",
    )
    op.drop_table("tenant_commercial_audit_events")
    op.drop_index(
        "ix_tenant_lifecycle_requests_enterprise_status",
        table_name="tenant_lifecycle_requests",
    )
    op.drop_table("tenant_lifecycle_requests")
    op.drop_index(
        "ix_tenant_provider_credentials_enterprise_status",
        table_name="tenant_provider_credentials",
    )
    op.drop_table("tenant_provider_credentials")
    op.drop_index(
        "ix_enterprise_commercial_profiles_enterprise",
        table_name="enterprise_commercial_profiles",
    )
    op.drop_table("enterprise_commercial_profiles")
    op.drop_index(
        "ix_enterprise_memberships_enterprise_status",
        table_name="enterprise_memberships",
    )
    op.drop_table("enterprise_memberships")
