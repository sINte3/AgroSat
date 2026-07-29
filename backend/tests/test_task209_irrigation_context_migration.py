"""Static and offline PostgreSQL tests for irrigation-context migration."""

import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations


BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "0008_irrigation_context.py"


def load():
    spec = importlib.util.spec_from_file_location(
        "task209_irrigation_context_migration",
        MIGRATION,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_extends_pixel_anomaly_head():
    module = load()
    assert module.revision == "0008_irrigation_context"
    assert module.down_revision == "0007_pixel_anomalies"


def test_upgrade_compiles_as_offline_postgresql_without_connection():
    module = load()
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with patch.object(module, "op", Operations(context)):
        module.upgrade()
    sql = output.getvalue()
    assert "CREATE TABLE irrigation_events" in sql
    assert "irrigation_context" in sql
    assert "uq_irrigation_events_enterprise_request" in sql
    assert "ix_irrigation_events_enterprise_field_occurred" in sql


def test_downgrade_compiles_offline_and_restores_source_constraint():
    module = load()
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with patch.object(module, "op", Operations(context)):
        module.downgrade()
    sql = output.getvalue()
    assert "DROP TABLE irrigation_events" in sql
    assert "DROP CONSTRAINT uq_field_inspections_id_field_enterprise" in sql
    assert "source IN ('attention_queue','manual')" in sql


def test_schema_enforces_tenant_field_inspection_and_recorder_links():
    source = MIGRATION.read_text(encoding="utf-8")
    for constraint in (
        "fk_irrigation_events_enterprise",
        "fk_irrigation_events_field_enterprise",
        "fk_irrigation_events_inspection_field_enterprise",
        "fk_irrigation_events_recorder",
        "uq_field_inspections_id_field_enterprise",
    ):
        assert constraint in source


def test_schema_enforces_vocabularies_units_idempotency_and_bounds():
    module = load()
    assert module.EVENT_TYPES == (
        "irrigation_applied",
        "irrigation_interrupted",
        "equipment_issue",
        "field_observation",
    )
    assert module.METHOD_CODES == (
        "canal",
        "drip",
        "sprinkler",
        "furrow",
        "manual",
        "unknown",
    )
    source = MIGRATION.read_text(encoding="utf-8")
    for constraint in (
        "ck_irrigation_events_event_type",
        "ck_irrigation_events_method_code",
        "ck_irrigation_events_evidence_source",
        "ck_irrigation_events_water_amount",
        "ck_irrigation_events_note",
        "ck_irrigation_events_request_fingerprint",
        "uq_irrigation_events_enterprise_request",
    ):
        assert constraint in source
    assert "water_amount_mm <= 1000" in source
    assert "evidence_source = 'human_reported'" in source


def test_live_downgrade_fails_before_changes_when_context_rows_exist():
    module = load()
    result = Mock()
    result.scalar_one.return_value = 1
    bind = Mock()
    bind.execute.return_value = result
    with (
        patch.object(
            module.op,
            "get_context",
            return_value=SimpleNamespace(as_sql=False),
        ),
        patch.object(module.op, "get_bind", return_value=bind),
        patch.object(module.op, "drop_index") as drop_index,
    ):
        with pytest.raises(RuntimeError, match="irrigation_context inspections"):
            module.downgrade()
    drop_index.assert_not_called()


def test_migration_has_no_model_import_backfill_or_database_write():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "from models" not in source
    assert "import models" not in source
    assert "create_all" not in source
    assert "UPDATE " not in source
    assert "INSERT " not in source
    assert "DELETE " not in source
