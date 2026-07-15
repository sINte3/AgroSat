"""Offline source and schema contracts for TASK_200."""
import importlib.util
from datetime import date, timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.field_inspections import router
from schemas.field_inspection import CreateInspectionRequest
from services.field_inspections import fingerprint

ROOT = Path(__file__).resolve().parents[1]


class FieldInspectionContracts(unittest.TestCase):
    def test_openapi_has_all_authenticated_routes(self):
        app = FastAPI(); app.include_router(router); schema = app.openapi()
        expected = {"/api/field-inspections", "/api/field-inspections/{inspection_id}",
                    "/api/field-inspections/{inspection_id}/start", "/api/field-inspections/{inspection_id}/complete",
                    "/api/field-inspections/{inspection_id}/cancel"}
        self.assertTrue(expected <= set(schema["paths"]))
        operations = [op for path in schema["paths"].values() for op in path.values()]
        self.assertEqual(7, len(operations))
        self.assertTrue(all(op.get("security") for op in operations))

    def test_missing_authentication_is_rejected_for_all_routes(self):
        client = TestClient(FastAPI())
        app = client.app; app.include_router(router); client = TestClient(app)
        calls = [("post","/api/field-inspections",{}),("get","/api/field-inspections",None),
                 ("get","/api/field-inspections/1",None),("patch","/api/field-inspections/1",{}),
                 ("post","/api/field-inspections/1/start",{}),("post","/api/field-inspections/1/complete",{}),
                 ("post","/api/field-inspections/1/cancel",{})]
        for method,url,body in calls:
            response=getattr(client,method)(url,json=body) if body is not None else getattr(client,method)(url)
            self.assertEqual(401,response.status_code)

    def test_migration_identity_and_scope(self):
        path=ROOT/"alembic/versions/0005_field_inspections.py"
        spec=importlib.util.spec_from_file_location("task200_migration",path); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.assertEqual("0005_field_inspections",module.revision)
        self.assertEqual("0004_repair_core_constraints",module.down_revision)
        source=path.read_text(encoding="utf-8")
        self.assertIn("uq_field_inspections_one_active_per_field",source)
        self.assertIn("status IN ('pending','in_progress')",source)
        self.assertNotIn("scouting"+"_notes",source)

    def test_source_rules_and_normalization(self):
        with self.assertRaises(ValidationError):
            CreateInspectionRequest(field_id=1,title="Inspect",source="attention_queue")
        with self.assertRaises(ValidationError):
            CreateInspectionRequest(field_id=1,title="Inspect",source="manual",source_priority="high")
        payload=CreateInspectionRequest(field_id=1,title="  Inspect  ",source="attention_queue",source_priority="high",source_attention_score=50,source_reason_codes=["water_stress","water_stress","active_alert"])
        self.assertEqual(["active_alert","water_stress"],payload.source_reason_codes)
        self.assertEqual("Inspect",payload.title)

    def test_fingerprint_is_deterministic(self):
        payload=CreateInspectionRequest(field_id=1,title="Inspect",source="attention_queue",source_priority="high",source_attention_score=50,source_reason_codes=["water_stress","active_alert"])
        self.assertEqual(fingerprint(7,payload,8),fingerprint(7,payload,8))
        self.assertEqual(64,len(fingerprint(7,payload,8)))

    def test_due_date_boundary_uses_tashkent_date(self):
        from services.field_inspections import _due, TASHKENT
        from datetime import datetime
        _due(datetime.now(TASHKENT).date())
        with self.assertRaises(Exception): _due(datetime.now(TASHKENT).date()-timedelta(days=1))

    def test_service_source_contract(self):
        source=(ROOT/"services/field_inspections.py").read_text(encoding="utf-8")
        self.assertNotIn("db.query",source); self.assertNotIn("relationship",source)
        self.assertIn("hashlib.sha256",source); self.assertIn("IntegrityError",source)
        self.assertIn("version=:ver",source); self.assertIn("assigned_to_id=:uid",source)
        self.assertIn("count(*) OVER()",source); self.assertNotIn("email",source.lower())
        self.assertNotIn("scouting"+"_notes",source)


if __name__ == "__main__": unittest.main()
