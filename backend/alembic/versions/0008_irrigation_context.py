"""Add tenant-scoped irrigation events and irrigation-context inspections.

Revision ID: 0008_irrigation_context
Revises: 0007_pixel_anomalies
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_irrigation_context"
down_revision: Union[str, Sequence[str], None] = "0007_pixel_anomalies"
branch_labels = None
depends_on = None

EVENT_TYPES = (
    "irrigation_applied",
    "irrigation_interrupted",
    "equipment_issue",
    "field_observation",
)
METHOD_CODES = (
    "canal",
    "drip",
    "sprinkler",
    "furrow",
    "manual",
    "unknown",
)


def quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.drop_constraint(
        "ck_field_inspections_source",
        "field_inspections",
        type_="check",
    )
    op.create_check_constraint(
        "ck_field_inspections_source",
        "field_inspections",
        "source IN ('attention_queue','manual','irrigation_context')",
    )
    op.create_unique_constraint(
        "uq_field_inspections_id_field_enterprise",
        "field_inspections",
        ["id", "field_id", "enterprise_id"],
    )

    op.create_table(
        "irrigation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("inspection_id", sa.Integer(), nullable=True),
        sa.Column("recorded_by_id", sa.Integer(), nullable=False),
        sa.Column("client_request_id", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method_code", sa.String(20), nullable=False),
        sa.Column("water_amount_mm", sa.Numeric(8, 2), nullable=True),
        sa.Column(
            "evidence_source",
            sa.String(30),
            nullable=False,
            server_default="human_reported",
        ),
        sa.Column("note", sa.Text(), nullable=True),
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
            ["enterprise_id"],
            ["enterprises.id"],
            name="fk_irrigation_events_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["field_id", "enterprise_id"],
            ["fields.id", "fields.enterprise_id"],
            name="fk_irrigation_events_field_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inspection_id", "field_id", "enterprise_id"],
            [
                "field_inspections.id",
                "field_inspections.field_id",
                "field_inspections.enterprise_id",
            ],
            name="fk_irrigation_events_inspection_field_enterprise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_id"],
            ["users.id"],
            name="fk_irrigation_events_recorder",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"event_type IN ({quoted(EVENT_TYPES)})",
            name="ck_irrigation_events_event_type",
        ),
        sa.CheckConstraint(
            f"method_code IN ({quoted(METHOD_CODES)})",
            name="ck_irrigation_events_method_code",
        ),
        sa.CheckConstraint(
            "evidence_source = 'human_reported'",
            name="ck_irrigation_events_evidence_source",
        ),
        sa.CheckConstraint(
            "water_amount_mm IS NULL OR "
            "(event_type = 'irrigation_applied' "
            "AND water_amount_mm > 0 AND water_amount_mm <= 1000)",
            name="ck_irrigation_events_water_amount",
        ),
        sa.CheckConstraint(
            "note IS NULL OR char_length(note) BETWEEN 3 AND 4000",
            name="ck_irrigation_events_note",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_irrigation_events_request_fingerprint",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_irrigation_events_version",
        ),
    )
    op.create_index(
        "uq_irrigation_events_enterprise_request",
        "irrigation_events",
        ["enterprise_id", "client_request_id"],
        unique=True,
    )
    op.create_index(
        "ix_irrigation_events_enterprise_field_occurred",
        "irrigation_events",
        ["enterprise_id", "field_id", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_irrigation_events_inspection_occurred",
        "irrigation_events",
        ["inspection_id", sa.text("occurred_at DESC")],
        postgresql_where=sa.text("inspection_id IS NOT NULL"),
    )


def downgrade() -> None:
    context = op.get_context()
    if not context.as_sql:
        count = op.get_bind().execute(
            sa.text(
                "SELECT count(*) FROM field_inspections "
                "WHERE source = 'irrigation_context'"
            )
        ).scalar_one()
        if count:
            raise RuntimeError(
                "Cannot downgrade while irrigation_context inspections exist"
            )

    op.drop_index(
        "ix_irrigation_events_inspection_occurred",
        table_name="irrigation_events",
    )
    op.drop_index(
        "ix_irrigation_events_enterprise_field_occurred",
        table_name="irrigation_events",
    )
    op.drop_index(
        "uq_irrigation_events_enterprise_request",
        table_name="irrigation_events",
    )
    op.drop_table("irrigation_events")
    op.drop_constraint(
        "uq_field_inspections_id_field_enterprise",
        "field_inspections",
        type_="unique",
    )
    op.drop_constraint(
        "ck_field_inspections_source",
        "field_inspections",
        type_="check",
    )
    op.create_check_constraint(
        "ck_field_inspections_source",
        "field_inspections",
        "source IN ('attention_queue','manual')",
    )
