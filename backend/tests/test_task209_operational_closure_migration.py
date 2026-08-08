"""Static and operation-shape tests for the unapplied closure migration."""

import importlib.util
import io
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations


BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "0006_operational_closure.py"


def load():
    spec = importlib.util.spec_from_file_location("task209_closure_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_extends_field_inspection_head():
    module = load()
    assert module.revision == "0006_operational_closure"
    assert module.down_revision == "0005_field_inspections"


def test_upgrade_creates_only_the_five_closure_tables():
    module = load()
    created = []
    with (
        patch.object(module.op, "alter_column") as alter_column,
        patch.object(module.op, "create_unique_constraint"),
        patch.object(
            module.op,
            "create_table",
            side_effect=lambda name, *args, **kwargs: created.append(name),
        ),
        patch.object(module.op, "create_index"),
    ):
        module.upgrade()
    assert alter_column.call_args.args == ("alembic_version", "version_num")
    assert alter_column.call_args.kwargs["type_"].length == 64
    assert created == [
        "inspection_results",
        "inspection_evidence",
        "corrective_actions",
        "action_verification_requests",
        "operational_audit_events",
    ]


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
    assert "CREATE TABLE inspection_results" in sql
    assert "CREATE TABLE operational_audit_events" in sql
    assert "geometry(POINT,4326)" in sql
    assert "CREATE UNIQUE INDEX uq_action_verification_one_awaiting" in sql


def test_migration_has_postgis_tenant_state_and_idempotency_constraints():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'geometry_type="POINT"' in source
    assert "srid=4326" in source
    assert "fk_inspection_results_inspection_enterprise" in source
    assert "fk_corrective_actions_result_enterprise" in source
    assert "ck_corrective_actions_closure_state" in source
    assert "ck_corrective_actions_reopen_state" in source
    assert "uq_operational_audit_actor_idempotency_key" in source
    assert "request_fingerprint ~ '^[0-9a-f]{64}$'" in source


def test_verification_constraints_prevent_wrong_source_and_wrong_date():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "ck_action_verification_record_type" in source
    assert "ck_action_verification_temporal_order" in source
    assert "ck_action_verification_quality_ranges" in source
    assert "verification_provenance" in source
    assert "observation_observed_at > reference_observed_at" in source
    assert "reference_date + minimum_separation_days" in source
    assert "uq_action_verification_one_awaiting" in source
    for code in ("ndvi", "savi", "evi", "ndmi", "ndre"):
        assert code in load().INDEX_CODES


def test_photo_metadata_is_bounded_and_binary_storage_is_absent():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "ck_inspection_evidence_photo_metadata" in source
    assert "byte_size BETWEEN 0 AND 26214400" in source
    assert "provider_reference" in source
    assert "LargeBinary" not in source
    assert "BYTEA" not in source


def test_downgrade_drops_children_before_parents():
    module = load()
    dropped = []
    with (
        patch.object(module.op, "alter_column") as alter_column,
        patch.object(module.op, "drop_index"),
        patch.object(
            module.op,
            "drop_table",
            side_effect=lambda name: dropped.append(name),
        ),
        patch.object(module.op, "drop_constraint"),
    ):
        module.downgrade()
    assert alter_column.call_args.args == ("alembic_version", "version_num")
    assert alter_column.call_args.kwargs["type_"].length == 32
    assert dropped == [
        "operational_audit_events",
        "action_verification_requests",
        "corrective_actions",
        "inspection_evidence",
        "inspection_results",
    ]


def test_migration_does_not_import_models_or_execute_data_backfill():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "from models" not in source
    assert "import models" not in source
    assert "create_all" not in source
    assert "op.execute" not in source
    assert "UPDATE field_inspections" not in source
