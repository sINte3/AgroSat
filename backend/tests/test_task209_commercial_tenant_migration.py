"""Offline PostgreSQL tests for the commercial tenant migration."""

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
    BACKEND / "alembic" / "versions" / "0012_commercial_tenant_boundary.py"
)


def load():
    spec = importlib.util.spec_from_file_location("task209_commercial", MIGRATION)
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


def test_revision_extends_variable_rate_head():
    module = load()
    assert module.revision == "0012_commercial_tenant_boundary"
    assert module.down_revision == "0011_variable_rate_recommendations"


def test_upgrade_and_downgrade_compile_offline():
    upgrade = compile_sql("upgrade")
    downgrade = compile_sql("downgrade")
    for table in (
        "enterprise_memberships",
        "enterprise_commercial_profiles",
        "tenant_provider_credentials",
        "tenant_lifecycle_requests",
        "tenant_commercial_audit_events",
    ):
        assert f"CREATE TABLE {table}" in upgrade
        assert f"DROP TABLE {table}" in downgrade


def test_every_tenant_table_has_enterprise_fk_and_leading_index():
    source = MIGRATION.read_text(encoding="utf-8")
    for marker in (
        "fk_enterprise_memberships_enterprise",
        "fk_enterprise_commercial_profiles_enterprise",
        "fk_tenant_provider_credentials_enterprise",
        "fk_tenant_lifecycle_requests_enterprise",
        "fk_tenant_commercial_audit_events_enterprise",
        "ix_enterprise_memberships_enterprise_status",
        "ix_enterprise_commercial_profiles_enterprise",
        "ix_tenant_provider_credentials_enterprise_status",
        "ix_tenant_lifecycle_requests_enterprise_status",
        "ix_tenant_commercial_audit_events_enterprise_created",
    ):
        assert marker in source


def test_constraints_cover_membership_credentials_lifecycle_and_audit():
    source = MIGRATION.read_text(encoding="utf-8")
    for marker in (
        "ck_enterprise_memberships_role",
        "ck_enterprise_memberships_status",
        "ck_enterprise_commercial_profiles_json",
        "ck_tenant_provider_credentials_reference",
        "ck_tenant_provider_credentials_status",
        "ck_tenant_lifecycle_requests_review",
        "ck_tenant_commercial_audit_events_details",
        "uq_tenant_lifecycle_requests_actor_request",
    ):
        assert marker in source


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
        with pytest.raises(RuntimeError, match="commercial tenant data"):
            module.downgrade()
    drop_index.assert_not_called()


def test_migration_does_not_backfill_or_import_models():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "from models" not in source
    assert "create_all" not in source
    assert "UPDATE " not in source
    assert "INSERT " not in source
    assert "DELETE " not in source
