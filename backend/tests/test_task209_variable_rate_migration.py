"""Offline PostgreSQL tests for the variable-rate migration."""

import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations


BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = (
    BACKEND
    / "alembic"
    / "versions"
    / "0011_variable_rate_recommendations.py"
)


def load():
    spec = importlib.util.spec_from_file_location("task209_variable_rate", MIGRATION)
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


def test_revision_extends_productivity_zone_head():
    module = load()
    assert module.revision == "0011_variable_rate_recommendations"
    assert module.down_revision == "0010_productivity_zones"


def test_upgrade_and_downgrade_compile_offline():
    upgrade = compile_sql("upgrade")
    downgrade = compile_sql("downgrade")
    assert "CREATE TABLE variable_rate_recommendations" in upgrade
    assert "CREATE TABLE variable_rate_recommendation_events" in upgrade
    assert "DROP TABLE variable_rate_recommendation_events" in downgrade
    assert downgrade.index(
        "DROP TABLE variable_rate_recommendation_events"
    ) < downgrade.index("DROP TABLE variable_rate_recommendations")


def test_schema_enforces_tenant_source_parent_and_actor_scope():
    source = MIGRATION.read_text(encoding="utf-8")
    for name in (
        "fk_variable_rate_recommendations_field_enterprise",
        "fk_variable_rate_recommendations_run_enterprise_field",
        "fk_variable_rate_recommendations_parent_enterprise_field",
        "fk_variable_rate_events_recommendation_enterprise_field",
        "uq_variable_rate_recommendations_actor_request",
        "uq_variable_rate_recommendations_version",
    ):
        assert name in source


def test_schema_enforces_human_bounds_kind_unit_safety_and_status():
    source = MIGRATION.read_text(encoding="utf-8")
    for name in (
        "ck_variable_rate_recommendations_kind_unit",
        "ck_variable_rate_recommendations_bounds",
        "ck_variable_rate_recommendations_safety_ack",
        "ck_variable_rate_recommendations_json_shapes",
        "ck_variable_rate_recommendations_status",
        "ck_variable_rate_recommendations_decision",
        "ck_variable_rate_events_type",
    ):
        assert name in source
    assert "zone_rates ?& ARRAY['low','medium','high']" in source
    assert "jsonb_object_length(zone_rates)=3" in source


def test_live_downgrade_fails_before_changes_when_rows_exist():
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
        with pytest.raises(RuntimeError, match="variable-rate recommendations"):
            module.downgrade()
    drop_index.assert_not_called()


def test_migration_has_no_model_import_backfill_or_product_database_write():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "from models" not in source
    assert "create_all" not in source
    assert "UPDATE " not in source
    assert "INSERT " not in source
    assert "DELETE " not in source
