"""Static and offline PostgreSQL tests for the unapplied anomaly migration."""

import importlib.util
import io
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations


BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "0007_pixel_anomalies.py"


def load():
    spec = importlib.util.spec_from_file_location("task209_anomaly_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_extends_operational_closure_head():
    module = load()
    assert module.revision == "0007_pixel_anomalies"
    assert module.down_revision == "0006_operational_closure"


def test_upgrade_creates_only_anomaly_tables_in_dependency_order():
    module = load()
    created = []
    with (
        patch.object(module.op, "create_unique_constraint"),
        patch.object(
            module.op,
            "create_table",
            side_effect=lambda name, *args, **kwargs: created.append(name),
        ),
        patch.object(module.op, "create_index"),
    ):
        module.upgrade()
    assert created == [
        "pixel_anomaly_runs",
        "pixel_anomalies",
        "pixel_anomaly_inspections",
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
    assert "CREATE TABLE pixel_anomaly_runs" in sql
    assert "CREATE TABLE pixel_anomalies" in sql
    assert "CREATE TABLE pixel_anomaly_inspections" in sql
    assert "geometry(MULTIPOLYGON,4326)" in sql
    assert "CREATE INDEX ix_pixel_anomalies_geometry" in sql


def test_downgrade_compiles_as_offline_postgresql_without_connection():
    module = load()
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with patch.object(module, "op", Operations(context)):
        module.downgrade()
    sql = output.getvalue()
    assert "DROP TABLE pixel_anomaly_inspections" in sql
    assert "DROP TABLE pixel_anomalies" in sql
    assert "DROP TABLE pixel_anomaly_runs" in sql
    assert "DROP CONSTRAINT uq_fields_id_enterprise_id" in sql


def test_migration_enforces_tenant_observation_and_inspection_scope():
    source = MIGRATION.read_text(encoding="utf-8")
    for constraint in (
        "uq_fields_id_enterprise_id",
        "uq_ndvi_records_id_field_id",
        "uq_satellite_index_records_id_field_index",
        "fk_pixel_anomaly_runs_field_enterprise",
        "fk_pixel_anomaly_runs_current_ndvi_field",
        "fk_pixel_anomaly_runs_current_satellite_field",
        "fk_pixel_anomalies_run_enterprise",
        "fk_pixel_anomaly_inspections_anomaly_enterprise",
        "fk_pixel_anomaly_inspections_inspection_enterprise",
        "fk_pixel_anomaly_inspections_field_enterprise",
    ):
        assert constraint in source


def test_migration_enforces_state_geometry_quality_and_deduplication():
    source = MIGRATION.read_text(encoding="utf-8")
    for constraint in (
        "ck_pixel_anomaly_runs_current_record",
        "ck_pixel_anomaly_runs_comparison_record",
        "ck_pixel_anomaly_runs_success_has_comparison",
        "ck_pixel_anomaly_runs_temporal_order",
        "ck_pixel_anomaly_runs_reason_codes",
        "uq_pixel_anomaly_runs_run_key",
        "uq_pixel_anomalies_run_zone_key",
        "ck_pixel_anomalies_geometry_valid",
        "ck_pixel_anomalies_score_confidence",
        "uq_pixel_anomaly_inspections_anomaly_id",
        "uq_pixel_anomaly_inspections_actor_idempotency",
    ):
        assert constraint in source
    assert 'geometry_type="MULTIPOLYGON"' in source
    assert "srid=4326" in source
    assert "NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry)" in source
    assert "No raster pixel arrays" not in source
    assert "LargeBinary" not in source
    assert "BYTEA" not in source


def test_index_and_classification_vocabularies_match_contract():
    module = load()
    assert module.INDEX_CODES == ("ndvi", "savi", "evi", "ndmi", "ndre")
    assert module.RUN_STATUSES == (
        "detected",
        "no_anomaly",
        "insufficient_data",
    )
    assert module.CLASSIFICATIONS == (
        "single_scene",
        "persistent",
        "recovering",
    )


def test_downgrade_drops_children_before_reference_constraints():
    module = load()
    dropped_tables = []
    dropped_constraints = []
    with (
        patch.object(module.op, "drop_index"),
        patch.object(
            module.op,
            "drop_table",
            side_effect=lambda name: dropped_tables.append(name),
        ),
        patch.object(
            module.op,
            "drop_constraint",
            side_effect=lambda name, *args, **kwargs: dropped_constraints.append(name),
        ),
    ):
        module.downgrade()
    assert dropped_tables == [
        "pixel_anomaly_inspections",
        "pixel_anomalies",
        "pixel_anomaly_runs",
    ]
    assert dropped_constraints == [
        "uq_satellite_index_records_id_field_index",
        "uq_ndvi_records_id_field_id",
        "uq_fields_id_enterprise_id",
    ]


def test_migration_does_not_import_models_or_execute_data_backfill():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "from models" not in source
    assert "import models" not in source
    assert "create_all" not in source
    assert "op.execute" not in source
    assert "UPDATE " not in source
    assert "INSERT " not in source
