import io, json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
from scripts import run_ndvi_collection_cycle as r


class CycleTests(unittest.TestCase):
    def args(self, *items): return r.parse_args(list(items))
    def child(self, code=0, mutate=None):
        def run(cmd, _timeout):
            summary = {'schema_version': 1, 'field_id': int(cmd[cmd.index('--field-id') + 1]), 'mode': cmd[-1][2:], 'date_from': cmd[cmd.index('--date-from') + 1], 'date_to': cmd[cmd.index('--date-to') + 1], 'exit_code': code, 'inserted_count': 0, 'skipped_existing_count': 0, 'quality_blocked_count': 0, 'error_count': 0}
            if mutate: mutate(summary)
            Path(cmd[cmd.index('--output-log') + 1]).write_text(json.dumps(summary), encoding='utf-8')
            return {'exit_code': code, 'timed_out': False, 'stdout': '', 'stderr': ''}
        return run
    def execute_run(self, args, **kwargs):
        with patch.object(r, 'acquire_lock', return_value='offline'), patch.object(r, 'release_lock'):
            return r.run(args, **kwargs)
    def test_default_scope_rejected(self): self.assertEqual(self.execute_run(self.args()), 2)
    def test_explicit_dry_run(self): self.assertEqual(self.execute_run(self.args('--field-ids', '1'), child_runner=self.child()), 0)
    def test_apply_requires_artifact_directory(self): self.assertEqual(self.execute_run(self.args('--apply', '--field-ids', '1'), child_runner=self.child()), 2)
    def test_id_deduplication(self): self.assertEqual(r.parse_ids('1,1,2'), [1,2])
    def test_rotation_wraps(self): self.assertEqual(r.select_batch([1,2,3], dict(r.state_default(), next_offset=2), 2)[0], [3,1])
    def test_retry_capacity_leaves_rotation_room(self): self.assertEqual(r.select_batch([1,2,3], dict(r.state_default(), retry_field_ids=[1,2]), 2)[0], [1,3])
    def test_active_change_drops_inactive_retry(self): self.assertEqual(r.select_batch([2,3], dict(r.state_default(), retry_field_ids=[1]), 2)[0], [2,3])
    def test_retry_old_failure_retained(self): self.assertEqual(r.retry_after(dict(r.state_default(), retry_field_ids=[1,9]), [1,2], [1]), [9,1])
    def test_retry_old_recovery_removed(self): self.assertEqual(r.retry_after(dict(r.state_default(), retry_field_ids=[1,9]), [1,2], []), [9])
    def test_retry_unselected_preserved(self): self.assertEqual(r.retry_after(dict(r.state_default(), retry_field_ids=[9]), [1], [1]), [9,1])
    def test_command_has_exact_mode_and_no_shell(self): self.assertEqual(r.command(1, __import__('datetime').date.today(), __import__('datetime').date.today(), 'apply', Path('C:/x'), Path('C:/l'))[-1], '--apply')
    def test_capture_reads_only_bound(self):
        class Stream(io.BytesIO):
            def read(self, size=-1): self.size=size; return super().read(size)
        stream=Stream(b'x'*100); self.assertEqual(len(r.read_capture(stream, 7)), 7); self.assertEqual(stream.size, 8)
    def test_capture_replaces_invalid_utf8(self): self.assertEqual(r.read_capture(io.BytesIO(b'\xff'), 1), '�')
    def test_sanitizer_redacts_all_required_forms(self):
        value=r.sanitize_text(r'\\?\C:\Users\Example User\a token=secret Authorization: Bearer xyz postgresql://u:p@host/db \Device\HarddiskVolume3\Users\Example User\x')
        self.assertNotIn('Example User', value); self.assertNotIn('secret', value); self.assertNotIn('xyz', value); self.assertNotIn('u:p', value)
    def test_child_schema_context_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'x'; path.write_text(json.dumps({'schema_version':1,'field_id':2,'mode':'dry-run','date_from':'2026-01-01','date_to':'2026-01-01','exit_code':0,'inserted_count':0,'skipped_existing_count':0,'quality_blocked_count':0,'error_count':0}))
            with self.assertRaises(r.ValidationError): r.parse_child(path, field_id=1)
    def test_ordinary_failure_exact_accounting(self):
        with tempfile.TemporaryDirectory() as directory:
            code=self.execute_run(self.args('--apply','--field-ids','1,2','--max-attempts','1','--output-dir',directory), child_runner=self.child(1)); summary=json.loads(next(Path(directory).glob('cycle_*/cycle_summary.json')).read_text())
            self.assertEqual(code,1); self.assertEqual(summary['failed_field_ids'],[1,2]); self.assertEqual(summary['success_count']+summary['failure_count']+summary['unattempted_count'],2)
    def test_fatal_accounting_and_no_state_advance(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)/'state.json'; code=self.execute_run(self.args('--apply','--all-active-fields','--batch-size','2','--state-file',str(state),'--output-dir',directory),field_query=lambda:[1,2],child_runner=self.child(2)); summary=json.loads(next(Path(directory).glob('cycle_*/cycle_summary.json')).read_text())
            self.assertEqual(code,2); self.assertEqual(summary['failed_field_ids'],[1]); self.assertEqual(summary['unattempted_field_ids'],[2]); self.assertFalse(state.exists())
    def test_success_writes_atomic_artifacts_and_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)/'state.json'; code=self.execute_run(self.args('--apply','--all-active-fields','--batch-size','2','--state-file',str(state),'--output-dir',directory),field_query=lambda:[1,2],child_runner=self.child()); run_dir=next(Path(directory).glob('cycle_*'))
            self.assertEqual(code,0); self.assertTrue(state.exists()); self.assertEqual(json.loads((run_dir/'cycle_summary.json').read_text())['exit_code'],code); self.assertIn('SUCCESS',(run_dir/'field_results.jsonl').read_text())
    def test_lock_release_failure_is_exit_four(self):
        with patch.object(r,'acquire_lock',return_value='x'),patch.object(r,'release_lock',side_effect=RuntimeError('x')): self.assertEqual(r.run(self.args('--field-ids','1'),child_runner=self.child()),4)
    def test_active_query_is_read_only_and_closed(self):
        class Result:
            def all(self): return [(1,), (2,)]
        class Session:
            def __init__(self): self.calls=[];self.rolled=False;self.closed=False
            def execute(self, statement): self.calls.append(str(statement));return Result()
            def rollback(self): self.rolled=True
            def close(self): self.closed=True
        session=Session(); import types
        with patch.dict(sys.modules, {'database':types.SimpleNamespace(SessionLocal=lambda:session)}): self.assertEqual(r.query_active(),[1,2])
        self.assertIn('SET TRANSACTION READ ONLY',session.calls[0]);self.assertIn('SELECT',session.calls[1]);self.assertTrue(session.rolled);self.assertTrue(session.closed);self.assertNotIn('COMMIT',' '.join(session.calls).upper())
    def test_child_schema_wrong_type_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x';p.write_text(json.dumps({'schema_version':'1'}))
            with self.assertRaises(r.ValidationError): r.parse_child(p)
    def test_child_schema_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x';p.write_text(json.dumps({'schema_version':2}))
            with self.assertRaises(r.ValidationError): r.parse_child(p)
    def test_child_json_reader_is_bounded_and_rejects_oversize(self):
        class Stream(io.BytesIO):
            def read(self,size=-1): self.size=size;return super().read(size)
        class File:
            def __init__(self): self.stream=Stream(b'x'*(r.CHILD_JSON_LIMIT+1))
            def __enter__(self): return self.stream
            def __exit__(self,*_): return False
        fake=File()
        with patch.object(Path,'open',return_value=fake):
            with self.assertRaises(r.ValidationError): r.read_child_json_bounded(Path('offline'))
        self.assertEqual(fake.stream.size,r.CHILD_JSON_LIMIT+1)
    def test_timeout_skips_child_parser_and_retries_to_success(self):
        calls=[]
        def runner(cmd,_):
            calls.append(cmd)
            if len(calls)==1:return {'exit_code':1,'timed_out':True,'stdout':'','stderr':''}
            return self.child()(cmd,_)
        with patch.object(r,'parse_child',wraps=r.parse_child) as parser:
            code=self.execute_run(self.args('--field-ids','1','--max-attempts','2'),child_runner=runner,sleeper=lambda _:None)
        self.assertEqual(code,0);self.assertEqual(parser.call_count,1)
    def test_timeout_exhaustion_is_ordinary_failure(self):
        def runner(_cmd,_): return {'exit_code':1,'timed_out':True,'stdout':'','stderr':''}
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)/'state.json';code=self.execute_run(self.args('--apply','--all-active-fields','--batch-size','2','--max-attempts','2','--state-file',str(state),'--output-dir',d),field_query=lambda:[1,2],child_runner=runner,sleeper=lambda _:None)
            summary=json.loads(next(Path(d).glob('cycle_*/cycle_summary.json')).read_text())
        self.assertEqual(code,1);self.assertEqual(summary['timeout_count'],4);self.assertEqual(summary['failed_field_ids'],[1,2]);self.assertEqual(summary['retry_queue_after'],[1,2])
    def test_lock_release_failure_restores_persisted_state_and_summary(self):
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)/'state.json'
            with patch.object(r,'acquire_lock',return_value='offline'),patch.object(r,'release_lock',side_effect=RuntimeError('offline')):
                code=r.run(self.args('--apply','--all-active-fields','--batch-size','2','--state-file',str(state),'--output-dir',d),field_query=lambda:[1,2],child_runner=self.child())
            summary=json.loads(next(Path(d).glob('cycle_*/cycle_summary.json')).read_text())
            self.assertEqual(code,4);self.assertEqual(summary['exit_code'],code);self.assertFalse(summary['state_advanced']);self.assertEqual(json.loads(state.read_text()),r.state_default())
    def test_restore_failure_is_diagnosed_and_remains_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)/'state.json'; original=r.atomic_json
            def flaky(path,data):
                if Path(path)==state and Path(path).exists(): raise OSError('restore offline')
                return original(path,data)
            with patch.object(r,'atomic_json',side_effect=flaky),patch.object(r,'acquire_lock',return_value='offline'),patch.object(r,'release_lock',side_effect=RuntimeError('offline')):
                code=r.run(self.args('--apply','--all-active-fields','--batch-size','2','--state-file',str(state),'--output-dir',d),field_query=lambda:[1,2],child_runner=self.child())
            summary=json.loads(next(Path(d).glob('cycle_*/cycle_summary.json')).read_text())
        self.assertEqual(code,4);self.assertTrue(summary['state_advanced']);self.assertIn('state restore failed',' '.join(summary['diagnostics']));self.assertEqual(summary['exit_code'],code)


def _case(name, action):
    def test(self): action(self)
    test.__name__='test_'+name
    return test


for _name, _action in {
 'invalid_id': lambda s: s.assertRaises(r.ValidationError,r.parse_ids,'0'),
 'too_many_ids': lambda s: s.assertRaises(r.ValidationError,r.parse_ids,','.join(map(str,range(101)))),
 'state_default': lambda s: s.assertEqual(r.state_default()['next_offset'],0),
 'state_invalid': lambda s: s.assertRaises(r.ValidationError,r.validate_state,{}),
 'date_future': lambda s: s.assertRaises(r.ValidationError,r.resolve_dates,None,'2999-01-01',1),
 'date_inverted': lambda s: s.assertRaises(r.ValidationError,r.resolve_dates,'2026-07-02','2026-07-01',1),
 'external_relative': lambda s: s.assertRaises(r.ValidationError,r.external,'relative','--x'),
 'child_negative_count': lambda s: s.assertRaises(r.ValidationError,r.parse_child,Path('missing')),
 'retry_deduplicates': lambda s: s.assertEqual(r.retry_after(dict(r.state_default(),retry_field_ids=[9]),[1],[9,1]),[9,1]),
 'select_empty': lambda s: s.assertEqual(r.select_batch([],r.state_default(),2)[0],[]),
 'dry_max_fields_validation': lambda s: s.assertEqual(s.execute_run(s.args('--max-fields','1'),field_query=lambda:[1],child_runner=s.child()),0),
 'process_summary_mismatch': lambda s: s.assertEqual(s.execute_run(s.args('--field-ids','1'),child_runner=s.child(0,lambda x:x.update(exit_code=1))),2),
 'child_bad_inserted': lambda s: s.assertEqual(s.execute_run(s.args('--field-ids','1'),child_runner=s.child(0,lambda x:x.update(inserted_count=2))),2),
 'child_bad_mode': lambda s: s.assertEqual(s.execute_run(s.args('--field-ids','1'),child_runner=s.child(0,lambda x:x.update(mode='write'))),2),
 'child_bad_date': lambda s: s.assertEqual(s.execute_run(s.args('--field-ids','1'),child_runner=s.child(0,lambda x:x.update(date_from='x'))),2),
 'child_internal_fatal': lambda s: s.assertEqual(s.execute_run(s.args('--field-ids','1,2'),child_runner=s.child(4)),4),
 'retry_exit_three': lambda s: s.assertEqual(s.execute_run(s.args('--field-ids','1','--max-attempts','2'),child_runner=s.child(3),sleeper=lambda _:None),1),
 'atomic_json_roundtrip': lambda s: _atomic_roundtrip(s),
 'jsonl_roundtrip': lambda s: _jsonl_roundtrip(s),
 'run_directory_failure': lambda s: _mkdir_failure(s),
 'field_results_failure': lambda s: _artifact_failure(s,'field_results.jsonl'),
 'summary_failure': lambda s: _artifact_failure(s,'cycle_summary.json'),
}.items(): setattr(CycleTests,'test_'+_name,_case(_name,_action))

def _atomic_roundtrip(s):
    with tempfile.TemporaryDirectory() as d: p=Path(d)/'x';r.atomic_json(p,{'a':1});s.assertEqual(json.loads(p.read_text())['a'],1)
def _jsonl_roundtrip(s):
    with tempfile.TemporaryDirectory() as d: p=Path(d)/'x';r.atomic_jsonl(p,[{'a':1}]);s.assertEqual(json.loads(p.read_text())['a'],1)
def _mkdir_failure(s):
    with tempfile.TemporaryDirectory() as d,patch.object(Path,'mkdir',side_effect=OSError('offline')): s.assertEqual(s.execute_run(s.args('--apply','--field-ids','1','--output-dir',d),child_runner=s.child()),2)
def _artifact_failure(s, suffix):
    with tempfile.TemporaryDirectory() as d:
        original=r.atomic_jsonl
        if suffix=='field_results.jsonl':
            with patch.object(r,'atomic_jsonl',side_effect=OSError('offline')): s.assertEqual(s.execute_run(s.args('--apply','--field-ids','1','--output-dir',d),child_runner=s.child()),2)
        else:
            with patch.object(r,'atomic_json',side_effect=OSError('offline')): s.assertEqual(s.execute_run(s.args('--apply','--field-ids','1','--output-dir',d),child_runner=s.child()),2)

def _restore_after_final_summary_failure(s):
    with tempfile.TemporaryDirectory() as d:
        state=Path(d)/'state.json'; original=r.atomic_json; calls=[]
        def flaky(path, data):
            calls.append(Path(path).name)
            if calls.count('cycle_summary.json') == 2: raise OSError('final summary offline')
            return original(path, data)
        with patch.object(r,'atomic_json',side_effect=flaky):
            code=s.execute_run(s.args('--apply','--all-active-fields','--batch-size','2','--state-file',str(state),'--output-dir',d),field_query=lambda:[1,2],child_runner=s.child())
        s.assertEqual(code,2); s.assertEqual(json.loads(state.read_text())['next_offset'],0)

setattr(CycleTests, 'test_final_summary_failure_restores_state', _case('final_summary_failure_restores_state', _restore_after_final_summary_failure))
