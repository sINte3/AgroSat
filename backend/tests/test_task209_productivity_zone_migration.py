"""Static and offline PostgreSQL tests for productivity-zone migration."""

import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations


BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "0010_productivity_zones.py"


def load():
    spec = importlib.util.spec_from_file_location(
        "task209_productivity_zone_migration",
        MIGRATION,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compile_sql(action):
    module = load()
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with patch.object(module, "op", Operations(context)):
        getattr(module, action)()
    return output.getvalue()


def test_revision_extends_yield_map_import_head():
    module = load()
    assert module.revision == "0010_productivity_zones"
    assert module.down_revision == "0009_yield_map_imports"
    assert module.RESULT_STATUSES == ("ready", "insufficient_data")
    assert module.ZONE_CLASSES == ("low", "medium", "high")


def test_upgrade_compiles_as_offline_postgresql_without_connection():
    sql = compile_sql("upgrade")
    assert "CREATE TABLE productivity_zone_runs" in sql
    assert "CREATE TABLE productivity_zones" in sql
    assert "geometry(MULTIPOLYGON,4326)" in sql
    assert "ix_productivity_zones_geometry" in sql
    assert "uq_productivity_zone_runs_key" in sql
    assert "uq_productivity_zones_run_class" in sql


def test_downgrade_drops_zones_before_runs():
    sql = compile_sql("downgrade")
    assert sql.index("DROP TABLE productivity_zones") < sql.index(
        "DROP TABLE productivity_zone_runs"
    )


def test_schema_enforces_tenant_ownership_and_deduplication():
    source = MIGRATION.read_text(encoding="utf-8")
    for name in (
        "fk_productivity_zone_runs_enterprise",
        "fk_productivity_zone_runs_field_enterprise",
        "fk_productivity_zones_run_enterprise_field",
        "fk_productivity_zones_field_enterprise",
        "uq_productivity_zone_runs_key",
        "uq_productivity_zones_run_class",
    ):
        assert name in source


def test_schema_enforces_status_geometry_confidence_and_area():
    source = MIGRATION.read_text(encoding="utf-8")
    for name in (
        "ck_productivity_zone_runs_status",
        "ck_productivity_zone_runs_key",
        "ck_productivity_zone_runs_confidence",
        "ck_productivity_zone_runs_result_invariants",
        "ck_productivity_zones_class",
        "ck_productivity_zones_measures",
        "ck_productivity_zones_geometry",
    ):
        assert name in source
    assert "zoned_area_ha <= field_area_ha + 0.01" in source
    assert "area_delta_ha = field_area_ha - zoned_area_ha" in source
    assert "NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry)" in source


def test_live_downgrade_fails_before_changes_when_runs_exist():
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
        with pytest.raises(RuntimeError, match="productivity zone runs"):
            module.downgrade()
    drop_index.assert_not_called()


def test_migration_has_no_model_import_backfill_or_product_database_write():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "from models" not in source
    assert "import models" not in source
    assert "create_all" not in source
    assert "UPDATE " not in source
    assert "INSERT " not in source
    assert "DELETE " not in source
