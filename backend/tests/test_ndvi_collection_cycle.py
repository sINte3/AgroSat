import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
BACKEND=Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:sys.path.insert(0,str(BACKEND))
from scripts import run_ndvi_collection_cycle as r
class TestCycle(unittest.TestCase):
 def args(self,*x):return r.parse_args(list(x))
 def child(self,code=0):
  def f(cmd,t):
   p=Path(cmd[cmd.index('--output-log')+1]);p.write_text(json.dumps({'exit_code':code,'inserted_count':0,'skipped_existing_count':0,'quality_blocked_count':0}));return {'exit_code':code,'timed_out':False,'stdout':'','stderr':''}
  return f
 def execute_run(self,args,**kw):
  with patch.object(r,'acquire_lock',return_value='x'),patch.object(r,'release_lock'):return r.run(args,**kw)
 def test_01_default_no_scope(self):self.assertEqual(self.execute_run(self.args()),2)
 def test_02_dry_explicit(self):self.assertEqual(self.execute_run(self.args('--field-ids','1'),child_runner=self.child()),0)
 def test_03_apply_scope(self):self.assertEqual(self.execute_run(self.args('--apply')),2)
 def test_04_write_scope(self):self.assertEqual(self.execute_run(self.args('--write')),2)
 def test_05_batch_low(self):self.assertEqual(self.execute_run(self.args('--all-active-fields','--batch-size','1')),2)
 def test_06_batch_high(self):self.assertEqual(self.execute_run(self.args('--all-active-fields','--batch-size','101')),2)
 def test_07_ids_dedupe(self):self.assertEqual(r.parse_ids('1,1,2'),[1,2])
 def test_08_ids_bound(self):self.assertRaises(r.ValidationError,r.parse_ids,','.join(map(str,range(1,102))))
 def test_09_state_default(self):self.assertEqual(r.load_state(Path(tempfile.gettempdir())/'missing_task188.json')['next_offset'],0)
 def test_10_state_bad(self):
  with tempfile.TemporaryDirectory() as d:p=Path(d)/'s';p.write_text('{}');self.assertRaises(r.ValidationError,r.load_state,p)
 def test_11_atomic(self):
  with tempfile.TemporaryDirectory() as d:p=Path(d)/'s';r.atomic_json(p,{'a':1});self.assertEqual(json.loads(p.read_text())['a'],1)
 def test_12_rotation(self):self.assertEqual(r.select_batch([1,2,3],r.state_default(),2)[0],[1,2])
 def test_13_wrap(self):self.assertEqual(r.select_batch([1,2,3],dict(r.state_default(),next_offset=2),2)[0],[3,1])
 def test_14_retry_reserved(self):self.assertEqual(r.select_batch([1,2,3],dict(r.state_default(),retry_field_ids=[1,2]),2)[0],[1,3])
 def test_15_active_change(self):self.assertEqual(r.select_batch([2,3],dict(r.state_default(),retry_field_ids=[1]),2)[0],[2,3])
 def test_16_lock_contention(self):
  with patch.object(r,'acquire_lock',side_effect=SystemExit(3)):self.assertEqual(r.run(self.args('--field-ids','1'),child_runner=self.child()),3)
 def test_17_child_command(self):
  x=r.command(9,__import__('datetime').date.today(),__import__('datetime').date.today(),'apply',Path('C:/x'),Path('C:/l'));self.assertEqual(x[0],sys.executable);self.assertIn('--skip-existing',x);self.assertNotIn('--force',x)
 def test_18_retry_one(self):self.assertEqual(self.execute_run(self.args('--field-ids','1','--max-attempts','2'),child_runner=self.child(1),sleeper=lambda _:None),1)
 def test_19_fatal_two(self):self.assertEqual(self.execute_run(self.args('--field-ids','1'),child_runner=self.child(2)),2)
 def test_20_retry_lock(self):self.assertEqual(self.execute_run(self.args('--field-ids','1','--max-attempts','2'),child_runner=self.child(3),sleeper=lambda _:None),1)
 def test_21_missing_child_log(self):self.assertEqual(self.execute_run(self.args('--field-ids','1'),child_runner=lambda a,b:{'exit_code':0}),2)
 def test_22_malformed_child_log(self):
  def f(a,b):Path(a[a.index('--output-log')+1]).write_text('bad');return {'exit_code':0}
  self.assertEqual(self.execute_run(self.args('--field-ids','1'),child_runner=f),2)
 def test_23_utf8_replacement(self):self.assertEqual(b'\xff'.decode('utf8','replace'),'�')
 def test_24_capture_bound(self):self.assertEqual(r.CAPTURE,16384)
 def test_25_external_paths(self):self.assertRaises(r.ValidationError,r.external,'relative','--x')
 def test_26_empty_active(self):self.assertEqual(self.execute_run(self.args('--all-active-fields','--batch-size','2'),field_query=lambda:[],child_runner=self.child()),2)
 def test_27_sanitization(self):self.assertNotIn('secret',r.sanitize_text('token=secret'))
 def test_28_exit_summary(self):
  with tempfile.TemporaryDirectory() as d:
   code=self.execute_run(self.args('--apply','--field-ids','1','--output-dir',d),child_runner=self.child());p=next(Path(d).glob('cycle_*/cycle_summary.json'));self.assertEqual(json.loads(p.read_text())['exit_code'],code)
 def test_29_state_write(self):
  with tempfile.TemporaryDirectory() as d:self.assertEqual(self.execute_run(self.args('--apply','--all-active-fields','--batch-size','2','--state-file',str(Path(d)/'s'),'--output-dir',d),field_query=lambda:[1,2],child_runner=self.child()),0)
 def test_30_no_scheduler_import(self):self.assertNotIn('APScheduler',(BACKEND/'scripts'/'run_ndvi_collection_cycle.py').read_text())
 def test_31_no_shell_true(self):self.assertNotIn('shell=True',(BACKEND/'scripts'/'run_ndvi_collection_cycle.py').read_text())
 def test_32_release_failure(self):
  with patch.object(r,'acquire_lock',return_value='x'),patch.object(r,'release_lock',side_effect=RuntimeError('x')):self.assertEqual(r.run(self.args('--field-ids','1'),child_runner=self.child()),4)
