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
    def test_safe_default_is_dry_run(self):
        args = cycle.parse_args([])
        self.assertEqual(cycle.validate_args(args)[0], "dry-run")

    def test_apply_write_require_scope(self):
        for flag in ("--apply", "--write"):
            with self.assertRaises(cycle.CycleValidationError):
                cycle.validate_args(cycle.parse_args([flag, "--output-dir", tempfile.gettempdir()]))

    def test_indices_and_field_ids_are_safe_and_normalized(self):
        self.assertEqual(cycle.parse_field_ids("16, 244,16,220"), [16, 244, 220])
        for value in ("ndvi", "wat", "savi,SAVI", " , "):
            with self.assertRaises(cycle.CycleValidationError): cycle.parse_indices(value)

    def test_all_active_requires_bounded_batch_and_external_state(self):
        with self.assertRaises(cycle.CycleValidationError):
            cycle.validate_args(cycle.parse_args(["--apply", "--all-active-fields", "--output-dir", tempfile.gettempdir()]))
        with tempfile.TemporaryDirectory() as directory:
            state = str(Path(directory) / "state.json")
            args = cycle.parse_args(["--apply", "--all-active-fields", "--batch-size", "2", "--state-file", state, "--output-dir", directory])
            self.assertEqual(cycle.validate_args(args)[0], "apply")

    def test_retry_and_output_bounds_are_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(cycle.CycleValidationError):
                cycle.validate_args(cycle.parse_args(["--apply", "--field-ids", "1", "--output-dir", directory, "--max-attempts", "6"]))
            with self.assertRaises(cycle.CycleValidationError):
                cycle.validate_args(cycle.parse_args(["--apply", "--field-ids", "1", "--output-dir", str(cycle.REPO_ROOT / "unsafe")]))

    def test_malformed_state_is_not_overwritten_and_atomic_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(cycle.CycleValidationError): cycle.load_state(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "not-json")
            cycle.atomic_json_write(path, cycle.default_state())
            self.assertEqual(cycle.load_state(path)["next_offset"], 0)
            self.assertFalse(list(Path(directory).glob("*.tmp")))

    def test_rotation_wraps_and_retry_does_not_starve(self):
        state = cycle.default_state()
        state["next_offset"] = 3
        selected, retries, rotation, offset = cycle.select_rotating_batch([10, 20, 30, 40], state, 2)
        self.assertEqual(selected, [40, 10]); self.assertEqual((retries, rotation, offset), (0, 2, 1))
        state.update({"next_offset": 0, "retry_field_ids": [20, 30]})
        selected, retries, rotation, _ = cycle.select_rotating_batch([10, 20, 30, 40], state, 3)
        self.assertEqual(selected, [20, 30, 10]); self.assertEqual((retries, rotation), (2, 1))

    def test_active_set_change_and_date_bounds(self):
        state = cycle.default_state(); state["next_offset"] = 99
        selected, _, _, offset = cycle.select_rotating_batch([4, 8], state, 1)
        self.assertEqual(selected, [8]); self.assertEqual(offset, 0)
        self.assertEqual(cycle.resolve_dates(None, None, 14, date(2026, 7, 14)), (date(2026, 6, 30), date(2026, 7, 14)))
        with self.assertRaises(cycle.CycleValidationError): cycle.resolve_dates("2026-01-01", "2026-07-14", 1, date(2026, 7, 14))

    def test_command_contract(self):
        command = cycle.build_child_command(7, ["savi", "evi"], date(2026, 7, 1), date(2026, 7, 2), "dry-run", Path("C:/logs/a.json"))
        self.assertEqual(command[0], sys.executable)
        self.assertTrue(Path(command[1]).is_absolute())
        self.assertIn("--max-fields", command); self.assertIn("--output-log", command)
        self.assertNotIn("--force", command); self.assertNotIn("--overwrite", command)

    def test_sanitizer_removes_secrets_user_paths_and_urls(self):
        value = "token=abc password: xyz postgres://user:pass@host/db C:\\Users\\alice\\secret"
        cleaned = cycle.sanitize_text(value)
        self.assertNotIn("abc", cleaned); self.assertNotIn("xyz", cleaned); self.assertNotIn("user:pass", cleaned); self.assertNotIn("alice", cleaned)


class CycleRunTests(unittest.TestCase):
    def run_with_child(self, codes, *, attempts=3):
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            args = cycle.parse_args(["--apply", "--field-ids", "4", "--output-dir", directory, "--max-attempts", str(attempts)])
            calls = []
            def child(command, timeout):
                calls.append((command, timeout))
                log = Path(command[command.index("--output-log") + 1])
                log.write_text(json.dumps({"db_inserted": 2, "db_skipped": 1, "quality_blocked": 3}), encoding="utf-8")
                return {"exit_code": codes[min(len(calls) - 1, len(codes) - 1)], "timed_out": False, "stdout": "", "stderr": ""}
            with patch.object(cycle, "acquire_lock", return_value="C:/cycle.lock"), patch.object(cycle, "release_lock"):
                result = cycle.run(args, child_runner=child, sleeper=lambda _: None)
            run_dir = next(directory_path.glob("cycle_*"))
            summary = json.loads((run_dir / "cycle_summary.json").read_text(encoding="utf-8"))
            return result, calls, summary

    def test_child_exit_one_retries_and_summary_aggregates(self):
        result, calls, summary = self.run_with_child([1, 0])
        self.assertEqual(result, 0); self.assertEqual(len(calls), 2)
        self.assertEqual(summary["inserted_count"], 4); self.assertEqual(summary["quality_blocked_count"], 6)

    def test_validation_exit_two_is_not_retried_and_partial_failure_is_one(self):
        result, calls, summary = self.run_with_child([2])
        self.assertEqual(result, 1); self.assertEqual(len(calls), 1); self.assertEqual(summary["exit_code"], 1)

    def test_timeout_retries_and_lock_maps_to_three(self):
        with tempfile.TemporaryDirectory() as directory:
            args = cycle.parse_args(["--apply", "--field-ids", "4", "--output-dir", directory, "--max-attempts", "2"])
            calls = []
            def child(command, timeout):
                calls.append(command)
                log = Path(command[command.index("--output-log") + 1]); log.write_text("{}", encoding="utf-8")
                return {"exit_code": 1, "timed_out": True, "stdout": "", "stderr": ""}
            with patch.object(cycle, "acquire_lock", return_value="C:/cycle.lock"), patch.object(cycle, "release_lock"):
                self.assertEqual(cycle.run(args, child_runner=child, sleeper=lambda _: None), 1)
            self.assertEqual(len(calls), 2)
            with patch.object(cycle, "acquire_lock", side_effect=SystemExit(3)):
                self.assertEqual(cycle.run(args), 3)

    def test_recovered_field_leaves_retry_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            cycle.atomic_json_write(state_path, {**cycle.default_state(), "retry_field_ids": [2]})
            args = cycle.parse_args(["--apply", "--all-active-fields", "--batch-size", "1", "--state-file", str(state_path), "--output-dir", directory])
            def child(command, timeout):
                Path(command[command.index("--output-log") + 1]).write_text("{}", encoding="utf-8")
                return {"exit_code": 0, "timed_out": False, "stdout": "", "stderr": ""}
            with patch.object(cycle, "acquire_lock", return_value="C:/cycle.lock"), patch.object(cycle, "release_lock"):
                self.assertEqual(cycle.run(args, field_query=lambda: [1, 2, 3], child_runner=child, sleeper=lambda _: None), 0)
            self.assertEqual(cycle.load_state(state_path)["retry_field_ids"], [])

    def test_subprocess_uses_shell_false(self):
        command = [sys.executable, "-c", "pass"]
        completed = type("Done", (), {"returncode": 0, "communicate": lambda self, timeout: ("", "")})()
        with patch("subprocess.Popen", return_value=completed) as runner:
            cycle.execute_child(command, 1)
        self.assertFalse(runner.call_args.kwargs["shell"])


if __name__ == "__main__":
    unittest.main()
