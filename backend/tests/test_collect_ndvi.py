import json, math, sys, tempfile, unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch
BACKEND=Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path: sys.path.insert(0,str(BACKEND))
from scripts import collect_ndvi as c

class CollectNdviTests(unittest.TestCase):
 def setUp(self):
  self._lock=patch.object(c,"acquire_lock",return_value="offline-lock");self._release=patch.object(c,"release_lock");self._lock.start();self._release.start()
 def tearDown(self): self._release.stop();self._lock.stop()
 def args(self,*x): return c.parse_args(list(x))
 def test_01_default_dry_run(self): self.assertEqual(c.run(self.args()),0)
 def test_02_dry_run_no_db_or_network(self):
  with patch.object(c,"field_lookup") as lookup: self.assertEqual(c.run(self.args()),0);lookup.assert_not_called()
 def test_03_apply_requires_field(self): self.assertEqual(c.run(self.args("--apply","--output-log",r"C:\tmp\x.json")),2)
 def test_04_write_requires_field(self): self.assertEqual(c.run(self.args("--write","--output-log",r"C:\tmp\x.json")),2)
 def test_05_future_date(self): self.assertRaises(c.ValidationError,c.dates,None,"2999-01-01",14)
 def test_06_inverted_dates(self): self.assertRaises(c.ValidationError,c.dates,"2026-07-10","2026-07-01",14)
 def test_07_range_bound(self): self.assertRaises(c.ValidationError,c.dates,"2026-05-01","2026-07-01",14)
 def test_08_missing_field(self):
  with tempfile.TemporaryDirectory() as d: self.assertEqual(c.run(self.args("--apply","--field-id","1","--output-log",str(Path(d)/"x.json")),lookup=lambda _: (_ for _ in ()).throw(c.ValidationError("missing"))),2)
 def test_09_mock_service_rejected(self):
  service=Mock(is_mock=True,source="Mock/Dev")
  with tempfile.TemporaryDirectory() as d:self.assertEqual(c.run(self.args("--apply","--field-id","1","--output-log",str(Path(d)/"x.json")),lookup=lambda _:{"geometry_wkt":"X"},service_factory=lambda:service),2)
 def test_10_invalid_provenance_rejected(self):
  service=Mock(is_mock=False,source="Sentinel-2");service.get_ndvi_stats.return_value={"satellite":"synthetic","mean_ndvi":.4,"captured_date":date.today().isoformat()}
  with tempfile.TemporaryDirectory() as d:self.assertEqual(c.run(self.args("--apply","--field-id","1","--output-log",str(Path(d)/"x.json")),lookup=lambda _:{"geometry_wkt":"X"},service_factory=lambda:service),2)
 def test_11_nonfinite_quality_blocked(self):
  service=Mock(is_mock=False,source="Sentinel-2");service.get_ndvi_stats.return_value={"satellite":"Sentinel-2","mean_ndvi":math.nan,"captured_date":date.today().isoformat()}
  with tempfile.TemporaryDirectory() as d:self.assertEqual(c.run(self.args("--apply","--field-id","1","--output-log",str(Path(d)/"x.json")),lookup=lambda _:{"geometry_wkt":"X"},service_factory=lambda:service),0)
 def test_12_out_of_range_quality_blocked(self):
  service=Mock(is_mock=False,source="Sentinel-2");service.get_ndvi_stats.return_value={"satellite":"Sentinel-2","mean_ndvi":2,"captured_date":date.today().isoformat()}
  with tempfile.TemporaryDirectory() as d:self.assertEqual(c.run(self.args("--apply","--field-id","1","--output-log",str(Path(d)/"x.json")),lookup=lambda _:{"geometry_wkt":"X"},service_factory=lambda:service),0)
 def test_13_writer_insert(self):
  service=Mock(is_mock=False,source="Sentinel-2");service.get_ndvi_stats.return_value={"satellite":"Sentinel-2","mean_ndvi":.5,"captured_date":date.today().isoformat()};writer=Mock(return_value=True)
  with tempfile.TemporaryDirectory() as d:self.assertEqual(c.run(self.args("--write","--field-id","1","--output-log",str(Path(d)/"x.json")),lookup=lambda _:{"geometry_wkt":"X"},service_factory=lambda:service,writer=writer),0);writer.assert_called_once()
 def test_14_writer_skip_existing(self):
  service=Mock(is_mock=False,source="Sentinel-2");service.get_ndvi_stats.return_value={"satellite":"Sentinel-2","mean_ndvi":.5,"captured_date":date.today().isoformat()};writer=Mock(return_value=False)
  with tempfile.TemporaryDirectory() as d:self.assertEqual(c.run(self.args("--write","--field-id","1","--output-log",str(Path(d)/"x.json")),lookup=lambda _:{"geometry_wkt":"X"},service_factory=lambda:service,writer=writer),0);self.assertEqual(json.loads((Path(d)/"x.json").read_text())["skipped_existing_count"],1)
 def test_15_lock_contention(self):
  with tempfile.TemporaryDirectory() as d,patch.object(c,"acquire_lock",side_effect=SystemExit(3)):self.assertEqual(c.run(self.args("--apply","--field-id","1","--output-log",str(Path(d)/"x.json"))),3)
 def test_16_mandatory_log_external(self): self.assertEqual(c.run(self.args("--apply","--field-id","1","--output-log","relative.json")),2)
 def test_17_summary_exit_equal(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/"x.json";code=c.run(self.args("--apply","--field-id","1","--output-log",str(p)),lookup=lambda _:(_ for _ in ()).throw(c.ValidationError("x")));self.assertEqual(json.loads(p.read_text())["exit_code"],code)
 def test_18_sanitizer(self): self.assertNotIn("secret",c.sanitize_text("password=secret https://u:p@host"))
 def test_19_no_force_argument(self): self.assertEqual(c.mode(self.args("--apply")),"apply")
 def test_20_invalid_payload_is_contract_error(self):
  service=Mock(is_mock=False,source="Sentinel-2");service.get_ndvi_stats.return_value=[]
  with tempfile.TemporaryDirectory() as d:self.assertEqual(c.run(self.args("--apply","--field-id","1","--output-log",str(Path(d)/"x")),lookup=lambda _:{"geometry_wkt":"X"},service_factory=lambda:service),2)
 def test_21_outside_captured_date_is_contract_error(self):
  service=Mock(is_mock=False,source="Sentinel-2");service.get_ndvi_stats.return_value={"satellite":"Sentinel-2","mean_ndvi":.5,"captured_date":"2000-01-01"}
  with tempfile.TemporaryDirectory() as d:self.assertEqual(c.run(self.args("--apply","--field-id","1","--output-log",str(Path(d)/"x")),lookup=lambda _:{"geometry_wkt":"X"},service_factory=lambda:service),2)
 def test_22_missing_captured_date_is_contract_error(self):
  service=Mock(is_mock=False,source="Sentinel-2");service.get_ndvi_stats.return_value={"satellite":"Sentinel-2","mean_ndvi":.5}
  with tempfile.TemporaryDirectory() as d:self.assertEqual(c.run(self.args("--apply","--field-id","1","--output-log",str(Path(d)/"x")),lookup=lambda _:{"geometry_wkt":"X"},service_factory=lambda:service),2)
 def test_23_persistence_exception_is_contract_error(self):
  service=Mock(is_mock=False,source="Sentinel-2");service.get_ndvi_stats.return_value={"satellite":"Sentinel-2","mean_ndvi":.5,"captured_date":date.today().isoformat()}
  with tempfile.TemporaryDirectory() as d:self.assertEqual(c.run(self.args("--write","--field-id","1","--output-log",str(Path(d)/"x")),lookup=lambda _:{"geometry_wkt":"X"},service_factory=lambda:service,writer=Mock(side_effect=RuntimeError("db"))),2)
 def test_24_sanitizer_windows_user_path(self): self.assertNotIn("Example User",c.sanitize_text(r"\\?\C:\Users\Example User\file"))
 def test_25_sanitizer_device_user_path(self): self.assertNotIn("Example User",c.sanitize_text(r"\Device\HarddiskVolume3\Users\Example User\file"))
 def test_26_sanitizer_authorization(self): self.assertNotIn("secret",c.sanitize_text("Authorization: Bearer secret token=secret"))
 def test_27_lock_release_is_reported(self):
  with tempfile.TemporaryDirectory() as d,patch.object(c,"release_lock",side_effect=RuntimeError("x")):
   code=c.run(self.args("--apply","--field-id","1","--output-log",str(Path(d)/"x")),lookup=lambda _:(_ for _ in ()).throw(c.ValidationError("x")));self.assertEqual(code,4);self.assertEqual(json.loads((Path(d)/"x").read_text())["exit_code"],code)
 def test_28_field_lookup_is_read_only_and_closed(self):
  class Result:
   def mappings(self): return self
   def first(self): return {"id":1,"geometry_wkt":"X"}
  class Session:
   def __init__(self): self.calls=[];self.rollback_called=False;self.closed=False
   def execute(self, statement, *values): self.calls.append(str(statement));return Result()
   def rollback(self): self.rollback_called=True
   def close(self): self.closed=True
  session=Session()
  import types
  with patch.dict(sys.modules,{"database":types.SimpleNamespace(SessionLocal=lambda:session)}):
   self.assertEqual(c.field_lookup(1)["id"],1)
  self.assertIn("SET TRANSACTION READ ONLY",session.calls[0]);self.assertIn("SELECT",session.calls[1]);self.assertTrue(session.rollback_called);self.assertTrue(session.closed);self.assertNotIn("COMMIT"," ".join(session.calls).upper())
 def test_29_field_lookup_rolls_back_when_select_fails(self):
  class Session:
   def __init__(self): self.rollback_called=False;self.closed=False
   def execute(self, statement, *values):
    if "SELECT" in str(statement): raise RuntimeError("offline")
   def rollback(self): self.rollback_called=True
   def close(self): self.closed=True
  session=Session();import types
  with patch.dict(sys.modules,{"database":types.SimpleNamespace(SessionLocal=lambda:session)}),self.assertRaises(RuntimeError): c.field_lookup(1)
  self.assertTrue(session.rollback_called);self.assertTrue(session.closed)
