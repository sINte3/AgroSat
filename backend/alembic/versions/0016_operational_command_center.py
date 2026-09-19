"""Operational Command Center notifications and append-only event history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0016_operational_command_center"
down_revision = "0015_closed_loop_agronomy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operational_notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=True),
        sa.Column("recipient_user_id", sa.Integer(), nullable=False),
        sa.Column("recipient_role_snapshot", sa.String(length=20), nullable=False),
        sa.Column("case_key", sa.String(length=180), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("notification_type", sa.String(length=48), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("payload_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="unread", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 255 AND "
            "char_length(btrim(message)) BETWEEN 1 AND 2000 AND "
            "jsonb_typeof(provenance)='object'",
            name="ck_operational_notifications_content",
        ),
        sa.CheckConstraint(
            "dedupe_key ~ '^[0-9a-f]{64}$' AND payload_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_operational_notifications_hashes",
        ),
        sa.CheckConstraint(
            "case_key ~ '^(inspection|candidate|alert|freshness|external):[A-Za-z0-9:_-]{1,160}$' "
            "AND source_id ~ '^[A-Za-z0-9:_-]{1,128}$'",
            name="ck_operational_notifications_identity",
        ),
        sa.CheckConstraint(
            "recipient_role_snapshot IN ('admin','manager','agronomist')",
            name="ck_operational_notifications_recipient_role",
        ),
        sa.CheckConstraint(
            "severity IN ('info','warning','critical')",
            name="ck_operational_notifications_severity",
        ),
        sa.CheckConstraint(
            "source_kind IN ('inspection','candidate','alert','agronomy_plan','agronomy_work_item','freshness','collection_run')",
            name="ck_operational_notifications_source_kind",
        ),
        sa.CheckConstraint(
            "status IN ('unread','read','dismissed','resolved') AND version >= 1",
            name="ck_operational_notifications_state",
        ),
        sa.CheckConstraint(
            "(status='unread' AND read_at IS NULL AND dismissed_at IS NULL AND resolved_at IS NULL) OR "
            "(status='read' AND read_at IS NOT NULL AND dismissed_at IS NULL AND resolved_at IS NULL) OR "
            "(status='dismissed' AND dismissed_at IS NOT NULL AND resolved_at IS NULL) OR "
            "(status='resolved' AND resolved_at IS NOT NULL AND dismissed_at IS NULL)",
            name="ck_operational_notifications_timestamps",
        ),
        sa.CheckConstraint(
            "notification_type IN ('new_critical','assignment','due_soon','overdue','missing_execution_evidence','awaiting_satellite_verification','external_source_unavailable')",
            name="ck_operational_notifications_type",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id"],
            ["enterprises.id"],
            name="fk_operational_notifications_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["field_id", "enterprise_id"],
            ["fields.id", "fields.enterprise_id"],
            name="fk_operational_notifications_field_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            ["users.id"],
            name="fk_operational_notifications_recipient",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "enterprise_id",
            "dedupe_key",
            name="uq_operational_notifications_dedupe",
        ),
        sa.UniqueConstraint(
            "id",
            "enterprise_id",
            name="uq_operational_notifications_id_enterprise",
        ),
    )
    op.create_index(
        "ix_operational_notifications_case",
        "operational_notifications",
        ["enterprise_id", "case_key", "status"],
        unique=False,
    )
    op.create_index(
        "ix_operational_notifications_recipient_inbox",
        "operational_notifications",
        ["recipient_user_id", "status", sa.text("created_at DESC"), sa.text("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_operational_notifications_reconcile",
        "operational_notifications",
        ["enterprise_id", "source_kind", "source_id", "notification_type"],
        unique=False,
    )

    op.create_table(
        "operational_notification_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("notification_id", sa.BigInteger(), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_key", sa.String(length=64), nullable=False),
        sa.Column("command_key", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column(
            "event_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("notification_version", sa.Integer(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(actor_id IS NULL AND actor_key='system:reconciler') OR "
            "(actor_id IS NOT NULL AND actor_key='user:'||actor_id::text)",
            name="ck_operational_notification_events_actor",
        ),
        sa.CheckConstraint(
            "notification_version >= 1 AND fingerprint ~ '^[0-9a-f]{64}$' "
            "AND jsonb_typeof(event_metadata)='object'",
            name="ck_operational_notification_events_content",
        ),
        sa.CheckConstraint(
            "to_status IN ('unread','read','dismissed','resolved') AND "
            "(from_status IS NULL OR from_status IN ('unread','read','dismissed','resolved'))",
            name="ck_operational_notification_events_states",
        ),
        sa.CheckConstraint(
            "event_type IN ('created','read','dismissed','resolved')",
            name="ck_operational_notification_events_type",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_operational_notification_events_actor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["notification_id", "enterprise_id"],
            ["operational_notifications.id", "operational_notifications.enterprise_id"],
            name="fk_operational_notification_events_notification",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_key",
            "command_key",
            name="uq_operational_notification_events_command",
        ),
    )
    op.create_index(
        "ix_operational_notification_events_timeline",
        "operational_notification_events",
        ["enterprise_id", "notification_id", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM operational_notifications) OR "
        "EXISTS (SELECT 1 FROM operational_notification_events) THEN "
        "RAISE EXCEPTION 'Operational notification history requires backup restore, not destructive downgrade'; "
        "END IF; END $$"
    )
    op.drop_table("operational_notification_events")
    op.drop_table("operational_notifications")
