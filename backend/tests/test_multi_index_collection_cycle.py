import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
from scripts import run_multi_index_collection_cycle as cycle


class CycleUnitTests(unittest.TestCase):
    def test_01_default_mode_is_dry_run(self): self.assertFalse(cycle.parse_args([]).apply); self.assertFalse(cycle.parse_args([]).write)
    def test_02_no_arguments_exit_two_before_query(self):
        self.assertEqual(cycle.run(cycle.parse_args([]), field_query=lambda: self.fail("DB queried")), 2)
    def test_03_dry_run_max_fields_is_bounded(self):
        cycle.validate_args(cycle.parse_args(["--max-fields", "1"]))
        for value in ("0", "26"):
            with self.assertRaises(cycle.CycleValidationError): cycle.validate_args(cycle.parse_args(["--max-fields", value]))
    def test_04_apply_write_require_scope(self):
        for flag in ("--apply", "--write"):
            with self.assertRaises(cycle.CycleValidationError): cycle.validate_args(cycle.parse_args([flag, "--output-dir", tempfile.gettempdir()]))
    def test_05_all_active_requires_batch_minimum_two(self):
        with self.assertRaises(cycle.CycleValidationError): cycle.validate_args(cycle.parse_args(["--all-active-fields", "--batch-size", "1"]))
    def test_06_external_paths_are_validated(self):
        for flag in ("--output-dir", "--lock-file"):
            with self.assertRaises(cycle.CycleValidationError): cycle.external_absolute_path("relative", flag, required=True)
        with self.assertRaises(cycle.CycleValidationError): cycle.external_absolute_path(str(cycle.REPO_ROOT / "x"), "--output-dir", True)
    def test_07_indices_reject_ndvi_unknown_empty_duplicate(self):
        for value in ("ndvi", "wat", "", "savi,SAVI"):
            with self.assertRaises(cycle.CycleValidationError): cycle.parse_indices(value)
    def test_08_field_ids_deduplicate_and_bound(self):
        self.assertEqual(cycle.parse_field_ids("1, 2,1"), [1, 2])
        with self.assertRaises(cycle.CycleValidationError): cycle.parse_field_ids(",".join(str(i) for i in range(1, 102)))
    def test_09_malformed_state_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"; path.write_text("bad", encoding="utf-8")
            with self.assertRaises(cycle.CycleValidationError): cycle.load_state(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "bad")
    def test_10_atomic_state_write(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"; cycle.atomic_json_write(path, cycle.default_state())
            self.assertEqual(cycle.load_state(path), cycle.default_state()); self.assertFalse(list(Path(d).glob("*.tmp")))
    def test_11_retry_cannot_consume_full_batch(self):
        state = {**cycle.default_state(), "retry_field_ids": [2, 3, 4]}
        selected, retries, rotation, _ = cycle.select_rotating_batch([1, 2, 3, 4, 5], state, 4)
        self.assertEqual(retries, 2); self.assertEqual(rotation, 2); self.assertEqual(len(selected), 4)
    def test_12_permanent_retries_do_not_starve_rotation(self):
        state = {**cycle.default_state(), "retry_field_ids": [1, 2]}; seen = set()
        for _ in range(4):
            selected, _, _, state["next_offset"] = cycle.select_rotating_batch([1, 2, 3, 4, 5], state, 2); seen.update(selected)
        self.assertTrue({3, 4, 5}.issubset(seen))
    def test_13_all_retry_case_is_bounded(self):
        state = {**cycle.default_state(), "retry_field_ids": [1, 2, 3]}
        self.assertEqual(cycle.select_rotating_batch([1, 2, 3], state, 2)[0], [1, 2])
    def test_14_cursor_uses_scanned_active_positions(self):
        state = {**cycle.default_state(), "retry_field_ids": [2], "next_offset": 0}
        self.assertEqual(cycle.select_rotating_batch([1, 2, 3, 4], state, 2)[3], 1)
    def test_15_active_set_change_is_safe(self):
        state = {**cycle.default_state(), "next_offset": 99, "retry_field_ids": [99]}
        self.assertEqual(cycle.select_rotating_batch([4, 8], state, 2)[0], [8, 4])
    def test_16_command_contract_has_no_force(self):
        cmd = cycle.build_child_command(7, ["savi"], date(2026, 7, 1), date(2026, 7, 2), "dry-run", Path("C:/x.json"))
        self.assertTrue(Path(cmd[1]).is_absolute()); self.assertNotIn("--force", cmd); self.assertNotIn("--overwrite", cmd)
    def test_17_utf8_replacement(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"ok\xff"); path = Path(f.name)
        try: self.assertIn("�", cycle._read_capture(path))
        finally: path.unlink()
    def test_18_output_retention_limit(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"x" * (cycle.CHILD_OUTPUT_LIMIT + 100)); path = Path(f.name)
        try: self.assertLessEqual(len(cycle._read_capture(path)), cycle.CHILD_OUTPUT_LIMIT)
        finally: path.unlink()
    def test_19_execute_child_cleans_capture_files(self):
        before = set(Path(tempfile.gettempdir()).glob("agrosat_cycle_std*"))
        cycle.execute_child([sys.executable, "-c", "print('ok')"], 5)
        self.assertEqual(before, set(Path(tempfile.gettempdir()).glob("agrosat_cycle_std*")))
    def test_20_recursive_sanitizer_removes_secrets_urls_paths(self):
        item = {"x": ["token=abc postgres://u:p@h/db", r"C:\Users\Jane Doe\x", r"\\?\C:\Users\Jane Doe\y"]}
        clean = json.dumps(cycle.sanitize_value(item)); self.assertNotIn("abc", clean); self.assertNotIn("u:p", clean); self.assertNotIn("Jane Doe", clean)


class CycleRunTests(unittest.TestCase):
    def run_cycle(self, codes=(0,), *, field_ids="4", attempts=3, child_json=True, all_active=False, release_error=None, timed_out=False):
        temp = tempfile.TemporaryDirectory(); root = Path(temp.name); args_list = ["--apply", "--output-dir", temp.name, "--max-attempts", str(attempts)]
        if all_active: args_list += ["--all-active-fields", "--batch-size", "2", "--state-file", str(root / "state.json")]
        else: args_list += ["--field-ids", field_ids]
        args = cycle.parse_args(args_list); calls = []
        def child(command, timeout):
            calls.append(command); log = Path(command[command.index("--output-log") + 1])
            if child_json: log.write_text(json.dumps({"db_inserted": 1}), encoding="utf-8")
            return {"exit_code": codes[min(len(calls)-1, len(codes)-1)], "timed_out": timed_out, "stdout": "", "stderr": ""}
        with patch.object(cycle, "acquire_lock", return_value="C:/cycle.lock"), patch.object(cycle, "release_lock", side_effect=release_error):
            result = cycle.run(args, field_query=lambda: [1, 2, 3, 4], child_runner=child, sleeper=lambda _: None)
        run_dir = next(root.glob("cycle_*")); summary_path = run_dir / "cycle_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else None
        return temp, root, result, calls, summary
    def test_21_ordinary_exit_one_retries(self):
        temp, _, result, calls, summary = self.run_cycle((1, 0)); self.addCleanup(temp.cleanup); self.assertEqual((result, len(calls), summary["exit_code"]), (0, 2, 0))
    def test_22_lock_exit_three_retries(self):
        temp, _, result, calls, _ = self.run_cycle((3, 0)); self.addCleanup(temp.cleanup); self.assertEqual((result, len(calls)), (0, 2))
    def test_23_timeout_retries(self):
        temp, root, result, calls, summary = self.run_cycle((1, 0), timed_out=True); self.addCleanup(temp.cleanup); self.assertEqual((result, len(calls), summary["timeout_count"]), (0, 2, 2))
    def test_24_child_exit_two_aborts(self):
        temp, _, result, calls, summary = self.run_cycle((2,), field_ids="4,5", attempts=1); self.addCleanup(temp.cleanup)
        self.assertEqual((result, len(calls), summary["exit_code"]), (2, 1, 2))
        self.assertEqual((summary["success_count"], summary["failure_count"], summary["unattempted_count"]), (0, 1, 1))
        self.assertEqual((summary["successful_field_ids"], summary["failed_field_ids"], summary["unattempted_field_ids"]), ([], [4], [5]))
    def test_25_malformed_child_json_aborts(self):
        temp, _, result, calls, summary = self.run_cycle((0,), child_json=False); self.addCleanup(temp.cleanup); self.assertEqual((result, len(calls), summary["exit_code"]), (2, 1, 2))
    def test_26_field_results_failure_is_two(self):
        with patch.object(cycle, "_write_results", side_effect=OSError("no")):
            temp, _, result, _, summary = self.run_cycle(); self.addCleanup(temp.cleanup); self.assertEqual((result, summary["exit_code"]), (2, 2))
    def test_27_summary_failure_is_two(self):
        original = cycle.atomic_json_write
        with patch.object(cycle, "atomic_json_write", side_effect=OSError("no")):
            temp, _, result, _, _ = self.run_cycle(); self.addCleanup(temp.cleanup); self.assertEqual(result, 2)
        cycle.atomic_json_write = original
    def test_28_state_failure_is_two_without_claimed_advancement(self):
        original = cycle.atomic_json_write; calls = []
        def writer(path, payload):
            calls.append(path)
            if Path(path).name == "state.json": raise OSError("no")
            return original(path, payload)
        with patch.object(cycle, "atomic_json_write", side_effect=writer):
            temp, _, result, _, summary = self.run_cycle(all_active=True); self.addCleanup(temp.cleanup); self.assertEqual((result, summary["state_advanced"]), (2, False))
    def test_29_empty_active_set_is_two(self):
        with tempfile.TemporaryDirectory() as d:
            args = cycle.parse_args(["--dry-run", "--all-active-fields", "--batch-size", "2"])
            self.assertEqual(cycle.run(args, field_query=lambda: []), 2)
    def test_30_temporary_dry_run_child_log_deleted(self):
        args = cycle.parse_args(["--dry-run", "--field-ids", "8"]); logs = []
        def child(cmd, _):
            log = Path(cmd[cmd.index("--output-log") + 1]); logs.append(log); log.write_text("{}", encoding="utf-8"); return {"exit_code": 0, "timed_out": False, "stdout": "", "stderr": ""}
        with patch.object(cycle, "acquire_lock", return_value="C:/lock"), patch.object(cycle, "release_lock"):
            self.assertEqual(cycle.run(args, child_runner=child), 0)
        self.assertFalse(logs[0].exists())
    def test_31_returned_code_matches_summary_codes(self):
        for code, expected in ((0, 0), (1, 1), (2, 2)):
            temp, _, result, _, summary = self.run_cycle((code,), attempts=1); self.addCleanup(temp.cleanup); self.assertEqual((result, summary["exit_code"]), (expected, expected))
    def test_32_lock_release_failure_is_surfaced(self):
        temp, _, result, _, summary = self.run_cycle(release_error=OSError("release")); self.addCleanup(temp.cleanup); self.assertEqual(result, 4); self.assertEqual(summary["exit_code"], 4)
    def test_33_no_real_db_network_sleep_or_scheduler(self):
        args = cycle.parse_args([])
        self.assertEqual(cycle.run(args, field_query=lambda: self.fail("query"), sleeper=lambda _: self.fail("sleep")), 2)
    def test_34_lock_contention_return_matches_summary(self):
        with tempfile.TemporaryDirectory() as d:
            args = cycle.parse_args(["--apply", "--field-ids", "4", "--output-dir", d])
            with patch.object(cycle, "acquire_lock", side_effect=SystemExit(3)):
                result = cycle.run(args)
            summary = json.loads(next(Path(d).glob("cycle_*/cycle_summary.json")).read_text(encoding="utf-8"))
            self.assertEqual((result, summary["exit_code"]), (3, 3))
    def test_35_orchestration_return_matches_summary(self):
        with tempfile.TemporaryDirectory() as d:
            args = cycle.parse_args(["--apply", "--field-ids", "4", "--output-dir", d])
            with patch.object(cycle, "acquire_lock", side_effect=OSError("launch")):
                result = cycle.run(args)
            summary = json.loads(next(Path(d).glob("cycle_*/cycle_summary.json")).read_text(encoding="utf-8"))
            self.assertEqual((result, summary["exit_code"]), (4, 4))


if __name__ == "__main__": unittest.main()
