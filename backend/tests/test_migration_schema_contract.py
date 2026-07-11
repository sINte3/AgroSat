"""Permanent static and metadata contract tests for the Alembic chain."""

import ast
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


BACKEND = Path(__file__).resolve().parents[1]
VERSIONS = BACKEND / "alembic" / "versions"
BASELINE = VERSIONS / "0001_baseline_existing_schema_baseline_existing_supabase_schema.py"
REPAIR = VERSIONS / "0004_repair_core_constraints.py"
IMMUTABLE_HASHES = {
    "0002_create_satellite_index_records.py": "0a264805efe5fb9a7d4c2a168a2f6c67751f28294ce9e28c07e419f317d22a34",
    "0003_add_ndvi_unique_constraint.py": "56c5068ffa460006100d33c29a3224bd34514ca236b54b8653f4d41944f2a69b",
    "478de3d1f6d0_add_alert_source_source_key.py": "0406463f76162a61718bc304230c84b65489fa1d4f66b0f96270a64ff0dbe950",
}


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MigrationSchemaContractTests(unittest.TestCase):
    def test_baseline_upgrade_is_not_empty(self):
        tree = ast.parse(BASELINE.read_text(encoding="utf-8"))
        upgrade = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
        self.assertGreater(len(upgrade.body), 1)
        self.assertFalse(any(isinstance(n, ast.Pass) for n in ast.walk(upgrade)))

    def test_baseline_owns_exactly_eight_core_tables(self):
        source = BASELINE.read_text(encoding="utf-8")
        expected = {"enterprises", "crop_types", "fields", "users", "crop_seasons", "ndvi_records", "alerts", "scouting_notes"}
        tree = ast.parse(source)
        created = {n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "create_table" and n.args and isinstance(n.args[0], ast.Constant)}
        self.assertEqual(expected, created)
        self.assertNotIn("satellite_index_records", source)

    def test_baseline_excludes_later_owned_objects(self):
        source = BASELINE.read_text(encoding="utf-8")
        self.assertNotIn("uq_ndvi_records_field_captured_date", source)
        self.assertNotIn('sa.Column("source"', source)
        self.assertNotIn("uq_alerts_active_source_source_key", source)

    def test_revision_chain_is_exactly_linear(self):
        paths = [BASELINE, VERSIONS / "0002_create_satellite_index_records.py", VERSIONS / "0003_add_ndvi_unique_constraint.py", VERSIONS / "478de3d1f6d0_add_alert_source_source_key.py", REPAIR]
        modules = [load(p) for p in paths]
        self.assertEqual([m.revision for m in modules], ["0001_baseline_existing_schema", "0002_create_satellite_index_records", "0003_add_ndvi_unique_constraint", "478de3d1f6d0", "0004_repair_core_constraints"])
        self.assertEqual([m.down_revision for m in modules], [None, "0001_baseline_existing_schema", "0002_create_satellite_index_records", "0003_add_ndvi_unique_constraint", "478de3d1f6d0"])

    def test_repair_prechecks_precede_ddl(self):
        module = load(REPAIR)
        events = []
        with patch.object(module, "_duplicate_group_count", side_effect=lambda sql: events.append("check") or 0), patch.object(module.op, "drop_index", side_effect=lambda *a, **k: events.append("ddl")), patch.object(module.op, "create_index"), patch.object(module.op, "create_unique_constraint"):
            module.upgrade()
        self.assertEqual(["check", "check", "ddl"], events)

    def test_repair_rejects_crop_duplicates_before_ddl(self):
        module = load(REPAIR)
        with patch.object(module, "_duplicate_group_count", return_value=1), patch.object(module.op, "drop_index") as ddl:
            with self.assertRaisesRegex(RuntimeError, "crop-season"):
                module.upgrade()
        ddl.assert_not_called()

    def test_repair_creates_unique_alert_index(self):
        module = load(REPAIR)
        with patch.object(module, "_duplicate_group_count", return_value=0), patch.object(module.op, "drop_index"), patch.object(module.op, "create_index") as create, patch.object(module.op, "create_unique_constraint"):
            module.upgrade()
        self.assertTrue(create.call_args.kwargs["unique"])
        self.assertEqual("uq_alerts_active_source_source_key", create.call_args.args[0])

    def test_repair_creates_named_crop_constraint(self):
        module = load(REPAIR)
        with patch.object(module, "_duplicate_group_count", return_value=0), patch.object(module.op, "drop_index"), patch.object(module.op, "create_index"), patch.object(module.op, "create_unique_constraint") as create:
            module.upgrade()
        create.assert_called_once_with("uq_crop_seasons_field_season_year", "crop_seasons", ["field_id", "season_year"])

    def test_repair_downgrade_restores_nonunique_index(self):
        module = load(REPAIR)
        with patch.object(module.op, "drop_constraint"), patch.object(module.op, "drop_index"), patch.object(module.op, "create_index") as create:
            module.downgrade()
        self.assertNotIn("unique", create.call_args.kwargs)

    def test_model_metadata_has_crop_constraint(self):
        from models.field import CropSeason
        constraints = {c.name: tuple(c.columns.keys()) for c in CropSeason.__table__.constraints}
        self.assertEqual(("field_id", "season_year"), constraints["uq_crop_seasons_field_season_year"])

    def test_model_metadata_has_alert_indexes(self):
        from models.monitoring import Alert
        indexes = {i.name: i for i in Alert.__table__.indexes}
        self.assertTrue(indexes["uq_alerts_active_source_source_key"].unique)
        self.assertIn("postgresql", indexes["uq_alerts_active_source_source_key"].dialect_options)
        self.assertIn("idx_alerts_field_active_severity", indexes)
        self.assertIn("ix_alerts_source_source_key", indexes)

    def test_model_metadata_has_ndvi_performance_index(self):
        from models.monitoring import NDVIRecord
        self.assertIn("idx_ndvi_records_field_date_desc", {i.name for i in NDVIRecord.__table__.indexes})

    def test_migrations_do_not_import_models_or_create_all(self):
        for path in VERSIONS.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("create_all", source, path.name)
            tree = ast.parse(source)
            imported = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
            self.assertFalse(any((getattr(n, "module", "") or "").startswith("models") for n in imported), path.name)

    def test_existing_migrations_remain_byte_identical(self):
        for name, expected in IMMUTABLE_HASHES.items():
            self.assertEqual(expected, hashlib.sha256((VERSIONS / name).read_bytes()).hexdigest())

    def test_only_allowed_paths_changed(self):
        import subprocess
        result = subprocess.run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=BACKEND.parent, text=True, capture_output=True, check=True)
        changed = {line[3:].replace("\\", "/") for line in result.stdout.splitlines()}
        allowed = {"backend/alembic/versions/0001_baseline_existing_schema_baseline_existing_supabase_schema.py", "backend/alembic/versions/0004_repair_core_constraints.py", "backend/models/field.py", "backend/models/monitoring.py", "backend/tests/test_migration_schema_contract.py"}
        self.assertEqual(allowed, changed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
