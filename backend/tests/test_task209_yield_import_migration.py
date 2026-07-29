"""Static and offline PostgreSQL tests for yield-map import migration."""

import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations


BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "0009_yield_map_imports.py"


def load():
    spec = importlib.util.spec_from_file_location(
        "task209_yield_map_import_migration",
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


def test_revision_extends_irrigation_context_head():
    module = load()
    assert module.revision == "0009_yield_map_imports"
    assert module.down_revision == "0008_irrigation_context"
    assert module.SCHEMA_CODES == ("yield_point_csv_v1",)
    assert module.INPUT_UNITS == ("t_ha", "kg_ha")


def test_upgrade_compiles_as_offline_postgresql_without_connection():
    sql = compile_sql("upgrade")
    assert "CREATE TABLE yield_map_imports" in sql
    assert "CREATE TABLE yield_map_points" in sql
    assert "geometry(POINT,4326)" in sql
    assert "ix_yield_map_points_geometry" in sql
    assert "uq_yield_map_imports_source" in sql
    assert "uq_yield_map_points_import_machine_point" in sql


def test_downgrade_compiles_and_drops_points_before_imports():
    sql = compile_sql("downgrade")
    assert sql.index("DROP TABLE yield_map_points") < sql.index(
        "DROP TABLE yield_map_imports"
    )


def test_schema_enforces_tenant_and_import_ownership():
    source = MIGRATION.read_text(encoding="utf-8")
    for name in (
        "fk_yield_map_imports_enterprise",
        "fk_yield_map_imports_field_enterprise",
        "fk_yield_map_imports_creator",
        "fk_yield_map_points_import_enterprise_field",
        "fk_yield_map_points_field_enterprise",
        "uq_yield_map_imports_actor_request",
    ):
        assert name in source


def test_schema_enforces_units_counts_geometry_and_quality():
    source = MIGRATION.read_text(encoding="utf-8")
    for name in (
        "ck_yield_map_imports_input_unit",
        "ck_yield_map_imports_counts_status",
        "ck_yield_map_imports_hashes",
        "ck_yield_map_imports_safe_filename",
        "ck_yield_map_points_yield",
        "ck_yield_map_points_speed",
        "ck_yield_map_points_moisture",
        "ck_yield_map_points_geometry",
    ):
        assert name in source
    assert "total_rows BETWEEN 1 AND 5000" in source
    assert "yield_t_ha BETWEEN 0.01 AND 100" in source
    assert "ST_X(geometry) BETWEEN -180 AND 180" in source
    assert "ST_Y(geometry) BETWEEN -90 AND 90" in source


def test_live_downgrade_fails_before_changes_when_imports_exist():
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
        with pytest.raises(RuntimeError, match="yield map imports"):
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
