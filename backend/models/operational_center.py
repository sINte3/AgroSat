"""Operational Center notification metadata; explicit SQL, no relationships."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from database import Base


NOTIFICATION_TYPES = (
    "new_critical",
    "assignment",
    "due_soon",
    "overdue",
    "missing_execution_evidence",
    "awaiting_satellite_verification",
    "external_source_unavailable",
)
SOURCE_KINDS = (
    "inspection",
    "candidate",
    "alert",
    "agronomy_plan",
    "agronomy_work_item",
    "freshness",
    "collection_run",
)
STATUSES = ("unread", "read", "dismissed", "resolved")
EVENT_TYPES = ("created", "read", "dismissed", "resolved")


def _quoted(values: tuple[str, ...]) -> str:
    return ",".join(repr(value) for value in values)


def register_operational_center() -> None:
    metadata = Base.metadata
    if "operational_notifications" in metadata.tables:
        return

    notification = sa.Table(
        "operational_notifications",
        metadata,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=True),
        sa.Column("recipient_user_id", sa.Integer(), nullable=False),
        sa.Column("recipient_role_snapshot", sa.String(20), nullable=False),
        sa.Column("case_key", sa.String(180), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("notification_type", sa.String(48), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "provenance",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        sa.Column("payload_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="unread"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "id",
            "enterprise_id",
            name="uq_operational_notifications_id_enterprise",
        ),
        sa.UniqueConstraint(
            "enterprise_id",
            "dedupe_key",
            name="uq_operational_notifications_dedupe",
        ),
        sa.CheckConstraint(
            f"source_kind IN ({_quoted(SOURCE_KINDS)})",
            name="ck_operational_notifications_source_kind",
        ),
        sa.CheckConstraint(
            f"notification_type IN ({_quoted(NOTIFICATION_TYPES)})",
            name="ck_operational_notifications_type",
        ),
        sa.CheckConstraint(
            "severity IN ('info','warning','critical')",
            name="ck_operational_notifications_severity",
        ),
        sa.CheckConstraint(
            f"status IN ({_quoted(STATUSES)}) AND version >= 1",
            name="ck_operational_notifications_state",
        ),
        sa.CheckConstraint(
            "recipient_role_snapshot IN ('admin','manager','agronomist')",
            name="ck_operational_notifications_recipient_role",
        ),
        sa.CheckConstraint(
            "case_key ~ '^(inspection|candidate|alert|freshness|external):[A-Za-z0-9:_-]{1,160}$' "
            "AND source_id ~ '^[A-Za-z0-9:_-]{1,128}$'",
            name="ck_operational_notifications_identity",
        ),
        sa.CheckConstraint(
            "dedupe_key ~ '^[0-9a-f]{64}$' AND payload_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_operational_notifications_hashes",
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 255 "
            "AND char_length(btrim(message)) BETWEEN 1 AND 2000 "
            "AND jsonb_typeof(provenance)='object'",
            name="ck_operational_notifications_content",
        ),
        sa.CheckConstraint(
            "(status='unread' AND read_at IS NULL AND dismissed_at IS NULL AND resolved_at IS NULL) OR "
            "(status='read' AND read_at IS NOT NULL AND dismissed_at IS NULL AND resolved_at IS NULL) OR "
            "(status='dismissed' AND dismissed_at IS NOT NULL AND resolved_at IS NULL) OR "
            "(status='resolved' AND resolved_at IS NOT NULL AND dismissed_at IS NULL)",
            name="ck_operational_notifications_timestamps",
        ),
    )
    sa.Index(
        "ix_operational_notifications_recipient_inbox",
        notification.c.recipient_user_id,
        notification.c.status,
        notification.c.created_at.desc(),
        notification.c.id.desc(),
    )
    sa.Index(
        "ix_operational_notifications_case",
        notification.c.enterprise_id,
        notification.c.case_key,
        notification.c.status,
    )
    sa.Index(
        "ix_operational_notifications_reconcile",
        notification.c.enterprise_id,
        notification.c.source_kind,
        notification.c.source_id,
        notification.c.notification_type,
    )

    event = sa.Table(
        "operational_notification_events",
        metadata,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("notification_id", sa.BigInteger(), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_key", sa.String(64), nullable=False),
        sa.Column("command_key", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=True),
        sa.Column(
            "event_metadata",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("notification_version", sa.Integer(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["notification_id", "enterprise_id"],
            ["operational_notifications.id", "operational_notifications.enterprise_id"],
            name="fk_operational_notification_events_notification",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_operational_notification_events_actor",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "actor_key",
            "command_key",
            name="uq_operational_notification_events_command",
        ),
        sa.CheckConstraint(
            f"event_type IN ({_quoted(EVENT_TYPES)})",
            name="ck_operational_notification_events_type",
        ),
        sa.CheckConstraint(
            f"to_status IN ({_quoted(STATUSES)}) AND "
            f"(from_status IS NULL OR from_status IN ({_quoted(STATUSES)}))",
            name="ck_operational_notification_events_states",
        ),
        sa.CheckConstraint(
            "notification_version >= 1 AND fingerprint ~ '^[0-9a-f]{64}$' "
            "AND jsonb_typeof(event_metadata)='object'",
            name="ck_operational_notification_events_content",
        ),
        sa.CheckConstraint(
            "(actor_id IS NULL AND actor_key='system:reconciler') OR "
            "(actor_id IS NOT NULL AND actor_key='user:'||actor_id::text)",
            name="ck_operational_notification_events_actor",
        ),
    )
    sa.Index(
        "ix_operational_notification_events_timeline",
        event.c.enterprise_id,
        event.c.notification_id,
        event.c.id,
    )


register_operational_center()
