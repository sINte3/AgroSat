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
OPERATIONAL_CLOSURE = VERSIONS / "0006_operational_closure.py"
IMMUTABLE_HASHES = {
    "0002_create_satellite_index_records.py": "82a17307b48100aba1c52b4ca22a66cbe007e22b4c029ff4efe3879d855ca08a",
    "0003_add_ndvi_unique_constraint.py": "45082a8806f4e75f8d8668457a28e4aca5fc7e2510da60fd365ed6392da32108",
    "478de3d1f6d0_add_alert_source_source_key.py": "827c4d8fc4c89a3be9f899e460b35451d8a8d9faff7e908c583a070ddb5020be",
}


def canonical_line_ending_sha256(content: bytes) -> str:
    canonical = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(canonical).hexdigest()


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MigrationSchemaContractTests(unittest.TestCase):
    def test_operational_closure_expands_alembic_revision_capacity(self):
        module = load(OPERATIONAL_CLOSURE)
        with patch.object(module.op, "alter_column") as alter:
            module._expand_alembic_version_capacity()

        self.assertEqual(("alembic_version", "version_num"), alter.call_args.args)
        self.assertEqual(module.ALEMBIC_VERSION_LENGTH, alter.call_args.kwargs["type_"].length)
        revisions = [load(path).revision for path in VERSIONS.glob("*.py")]
        self.assertLessEqual(max(map(len, revisions)), module.ALEMBIC_VERSION_LENGTH)

    def test_operational_closure_downgrade_restores_legacy_capacity(self):
        module = load(OPERATIONAL_CLOSURE)
        with patch.object(module.op, "alter_column") as alter:
            module._restore_alembic_version_capacity()

        self.assertEqual(
            module.LEGACY_ALEMBIC_VERSION_LENGTH,
            alter.call_args.kwargs["type_"].length,
        )

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

    def test_existing_migrations_remain_content_identical_across_line_endings(self):
        for name, expected in IMMUTABLE_HASHES.items():
            checked_out = (VERSIONS / name).read_bytes()
            synthetic_lf = checked_out.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            synthetic_crlf = synthetic_lf.replace(b"\n", b"\r\n")
            mutation_index = next(index for index, byte in enumerate(synthetic_lf) if byte not in b"\r\n")
            mutated = bytearray(synthetic_lf)
            mutated[mutation_index] ^= 1

            self.assertEqual(expected, canonical_line_ending_sha256(checked_out))
            self.assertEqual(expected, canonical_line_ending_sha256(synthetic_lf))
            self.assertEqual(expected, canonical_line_ending_sha256(synthetic_crlf))
            self.assertNotEqual(expected, canonical_line_ending_sha256(bytes(mutated)))

    def test_repair_offline_upgrade_skips_duplicate_queries_and_executes_ddl(self):
        module = load(REPAIR)
        bind = Mock()
        with patch.object(module.context, "is_offline_mode", return_value=True), patch.object(
            module.op, "get_bind", return_value=bind
        ) as get_bind, patch.object(module.op, "drop_index") as drop, patch.object(
            module.op, "create_index"
        ) as create_index, patch.object(module.op, "create_unique_constraint") as create_constraint:
            module.upgrade()

        get_bind.assert_not_called()
        bind.execute.assert_not_called()
        drop.assert_called_once_with(
            "uq_alerts_active_source_source_key", table_name="alerts"
        )
        create_index.assert_called_once()
        self.assertTrue(create_index.call_args.kwargs["unique"])
        self.assertEqual(
            "uq_alerts_active_source_source_key", create_index.call_args.args[0]
        )
        create_constraint.assert_called_once_with(
            "uq_crop_seasons_field_season_year",
            "crop_seasons",
            ["field_id", "season_year"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
