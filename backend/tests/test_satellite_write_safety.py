"""Regression tests for fail-closed satellite write safety."""

import argparse
import asyncio
import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class RedStageConfirmedDefects(unittest.TestCase):
    def test_missing_credentials_fail_closed_instead_of_mock(self):
        from config import settings
        from services.satellite import get_satellite_service

        with patch.object(settings, "sentinel_hub_client_id", ""), patch.object(
            settings, "sentinel_hub_client_secret", ""
        ):
            with self.assertRaises(RuntimeError):
                get_satellite_service()

    def test_mock_write_mode_rejected_before_protected_operations(self):
        from scripts import collect_satellite_indices as collector

        args = collector.parse_args(
            ["--write", "--mock-sentinel", "--field-id", "1", "--index", "savi"]
        )
        with patch.object(collector, "run_real") as run_real:
            with patch.object(sys, "argv", ["collector", "--write", "--mock-sentinel", "--field-id", "1", "--index", "savi"]):
                with self.assertRaises(SystemExit) as raised:
                    collector.main()
        self.assertEqual(raised.exception.code, 2)
        run_real.assert_not_called()

    def test_insert_rejects_mock_before_session_construction(self):
        from scripts import collect_satellite_indices as collector

        with patch.object(collector, "SessionLocal") as session:
            with self.assertRaises(RuntimeError):
                collector._insert_satellite_index_record(
                    field_id=1,
                    captured_date=__import__("datetime").date.today(),
                    index_code="savi",
                    mean_value=0.5,
                    min_value=0.4,
                    max_value=0.6,
                    std_value=0.1,
                    p10_value=0.4,
                    p90_value=0.6,
                    valid_pixels_pct=90.0,
                    cloud_cover_pct=5.0,
                    satellite="Mock/Dev",
                )
        session.assert_not_called()

    def test_api_refresh_does_not_use_implicit_mock_service(self):
        source = (BACKEND / "api" / "ndvi.py").read_text(encoding="utf-8")
        self.assertNotIn("from services.satellite import satellite_service", source)


class CredentialAndFactoryTests(unittest.TestCase):
    def setUp(self):
        from config import settings
        self.settings = settings

    def factory(self, client_id, secret, allow_mock=False):
        from services.satellite import get_satellite_service
        with patch.object(self.settings, "sentinel_hub_client_id", client_id), patch.object(
            self.settings, "sentinel_hub_client_secret", secret
        ):
            return get_satellite_service(allow_mock=allow_mock)

    def test_complete_credentials_produce_real_service(self):
        service = self.factory("valid-id", "valid-secret")
        self.assertFalse(service.is_mock)

    def test_both_missing_fail_closed(self):
        with self.assertRaises(RuntimeError): self.factory("", "")

    def test_client_id_only_fails_closed(self):
        with self.assertRaises(RuntimeError): self.factory("valid-id", "")

    def test_client_secret_only_fails_closed(self):
        with self.assertRaises(RuntimeError): self.factory("", "valid-secret")

    def test_whitespace_only_is_missing(self):
        with self.assertRaises(RuntimeError): self.factory("  ", "\t")

    def test_surrounding_whitespace_is_invalid(self):
        with self.assertRaises(RuntimeError): self.factory(" valid-id", "valid-secret")

    def test_explicit_missing_credentials_mock(self):
        self.assertTrue(self.factory("", "", allow_mock=True).is_mock)

    def test_partial_credentials_never_mock(self):
        with self.assertRaises(RuntimeError): self.factory("valid-id", "", allow_mock=True)

    def test_satellite_import_has_no_singleton(self):
        import services.satellite as module
        self.assertFalse(hasattr(module, "satellite_service"))

    def test_main_import_without_credentials(self):
        with patch.object(self.settings, "sentinel_hub_client_id", ""), patch.object(
            self.settings, "sentinel_hub_client_secret", ""
        ):
            self.assertIsNotNone(importlib.import_module("main"))


class ProvenanceTests(unittest.TestCase):
    def test_canonical_real_source_accepted(self):
        from services.satellite_safety import require_real_provenance
        self.assertEqual(require_real_provenance("Sentinel-2"), "Sentinel-2")

    def test_empty_source_rejected(self):
        from services.satellite_safety import require_real_provenance
        with self.assertRaises(RuntimeError): require_real_provenance("")

    def test_missing_source_rejected(self):
        from services.satellite_safety import require_real_provenance
        with self.assertRaises(RuntimeError): require_real_provenance(None)

    def test_mock_source_rejected(self):
        from services.satellite_safety import require_real_provenance
        with self.assertRaises(RuntimeError): require_real_provenance("Mock/Dev")

    def test_mixed_case_mock_rejected(self):
        from services.satellite_safety import require_real_provenance
        with self.assertRaises(RuntimeError): require_real_provenance("mOcK/deV")

    def test_synthetic_source_rejected(self):
        from services.satellite_safety import require_real_provenance
        with self.assertRaises(RuntimeError): require_real_provenance("synthetic")

    def test_test_source_rejected(self):
        from services.satellite_safety import require_real_provenance
        with self.assertRaises(RuntimeError): require_real_provenance("test")

    def test_batch_rejected_before_session(self):
        from scripts import write_multi_index_single_field as writer
        records = [{"satellite": "Sentinel-2"}, {"satellite": "Mock/Dev"}]
        with patch.object(writer, "SessionLocal") as session:
            with self.assertRaises(RuntimeError): writer.do_write(1, records)
        session.assert_not_called()

    def test_fetch_rejects_mock_before_execute(self):
        from services.satellite import MockSatelliteService, fetch_ndvi_for_field_date
        db = Mock()
        with self.assertRaises(RuntimeError):
            fetch_ndvi_for_field_date(db, Mock(), __import__("datetime").date.today(), service=MockSatelliteService())
        db.execute.assert_not_called()


class CollectorModeTests(unittest.TestCase):
    def args(self, *flags):
        from scripts.collect_satellite_indices import parse_args
        return parse_args(list(flags) + ["--field-id", "1", "--index", "savi"])

    def assert_rejected(self, *flags):
        from scripts.collect_satellite_indices import validate_collection_mode
        with self.assertRaises(SystemExit) as raised: validate_collection_mode(self.args(*flags))
        self.assertEqual(raised.exception.code, 2)

    def test_write_mock_rejected(self): self.assert_rejected("--write", "--mock-sentinel")
    def test_force_mock_rejected(self): self.assert_rejected("--force", "--mock-sentinel")
    def test_write_no_sentinel_rejected(self): self.assert_rejected("--write", "--no-sentinel")
    def test_apply_no_sentinel_rejected(self): self.assert_rejected("--apply", "--no-sentinel")

    def test_apply_mock_is_no_write(self):
        from scripts.collect_satellite_indices import validate_collection_mode
        args = self.args("--apply", "--mock-sentinel")
        validate_collection_mode(args)
        self.assertFalse(args.write or args.force)

    def test_insert_real_reaches_mocked_boundary(self):
        from scripts import collect_satellite_indices as collector
        with patch.object(collector, "check_exists_by_key", return_value=False), patch.object(
            collector, "SessionLocal"
        ) as session:
            session.return_value.execute.return_value = None
            self.assertTrue(collector._insert_satellite_index_record(
                1, __import__("datetime").date.today(), "savi", 0.5, "Sentinel-2"
            ))
        session.assert_called_once()

    def test_bypassed_validator_mock_write_rejected(self):
        from scripts import collect_satellite_indices as collector
        args = self.args("--write", "--mock-sentinel")
        with patch.object(collector, "acquire_lock") as lock:
            with self.assertRaises(RuntimeError): collector.run_real(args)
        lock.assert_not_called()


class StaticContractTests(unittest.TestCase):
    def python_source(self):
        return "\n".join(
            p.read_text(encoding="utf-8") for p in BACKEND.rglob("*.py")
            if "tests" not in p.parts
        )

    def test_no_satellite_singleton_import(self):
        self.assertNotIn("from services.satellite import satellite_service", self.python_source())

    def test_no_module_level_satellite_singleton(self):
        source = (BACKEND / "services" / "satellite.py").read_text(encoding="utf-8")
        self.assertNotIn("satellite_service = get_satellite_service()", source)

    def test_no_mock_write_label(self):
        self.assertNotIn("MOCK + WRITE", self.python_source())

    def test_no_sentinel_not_mock_factory(self):
        source = (BACKEND / "scripts" / "collect_satellite_indices.py").read_text(encoding="utf-8")
        self.assertNotIn("if mock_sentinel or no_sentinel", source)

    def test_writers_use_batch_guard(self):
        for name in ("write_multi_index_single_field.py", "write_multi_index_batch.py"):
            source = (BACKEND / "scripts" / name).read_text(encoding="utf-8")
            self.assertIn("validate_batch_provenance(records)", source)


class ApiSchedulerScriptTests(unittest.TestCase):
    def test_refresh_missing_credentials_503_before_db(self):
        from api.ndvi import refresh_ndvi
        from services.satellite_safety import SatelliteConfigurationError
        from fastapi import HTTPException
        db = Mock()
        with patch("services.satellite.get_satellite_service", side_effect=SatelliteConfigurationError()):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(refresh_ndvi(1, db=db, _auth_field=Mock(name="field")))
        self.assertEqual(raised.exception.status_code, 503)
        db.execute.assert_not_called()
        db.rollback.assert_not_called()

    def test_scheduler_missing_credentials_before_session(self):
        import scheduler
        from services.satellite_safety import SatelliteConfigurationError
        with patch("services.satellite.get_satellite_service", side_effect=SatelliteConfigurationError()), patch(
            "database.SessionLocal"
        ) as session:
            scheduler.fetch_all_fields_ndvi()
        session.assert_not_called()

    def test_fetch_all_missing_credentials_before_init_db(self):
        from scripts import fetch_all_ndvi
        from services.satellite_safety import SatelliteConfigurationError
        with patch("services.satellite.get_satellite_service", side_effect=SatelliteConfigurationError()), patch.object(
            fetch_all_ndvi, "init_db"
        ) as init_db:
            with self.assertRaises(SatelliteConfigurationError): fetch_all_ndvi.fetch_ndvi_for_all()
        init_db.assert_not_called()

    def test_batch_writer_rejects_unsafe_before_session(self):
        from scripts import write_multi_index_batch as writer
        with patch.object(writer, "SessionLocal") as session:
            with self.assertRaises(RuntimeError):
                writer._do_field_write(1, [{"satellite": "synthetic"}])
        session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
