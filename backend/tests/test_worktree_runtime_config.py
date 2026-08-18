"""Regression tests for verified worktree runtime configuration."""

import ast
import builtins
import os
from pathlib import Path
import re
import subprocess
import unittest
from unittest.mock import patch


BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
CONFIG = BACKEND / "config.py"
LAUNCHER = ROOT / "start.bat"
RUNTIME_VARIABLE = "AGROSAT_RUNTIME_ENV_FILE"


class RuntimeEnvFileTests(unittest.TestCase):
    def setUp(self):
        import config

        self.config = config

    def test_no_override_preserves_backend_env_file(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(RUNTIME_VARIABLE, None)
            result = self.config._resolve_runtime_env_file()
        self.assertEqual(result, self.config.BACKEND_DIR / ".env")

    def test_absolute_existing_override_is_accepted(self):
        with patch.dict(os.environ, {RUNTIME_VARIABLE: str(CONFIG)}):
            result = self.config._resolve_runtime_env_file()
        self.assertEqual(result, CONFIG.resolve())

    def test_relative_override_fails_closed(self):
        with patch.dict(os.environ, {RUNTIME_VARIABLE: "relative.env"}):
            with self.assertRaises(RuntimeError):
                self.config._resolve_runtime_env_file()

    def test_missing_override_fails_closed(self):
        missing = ROOT / "task178-missing-runtime.env"
        self.assertFalse(missing.exists())
        with patch.dict(os.environ, {RUNTIME_VARIABLE: str(missing.resolve())}):
            with self.assertRaises(RuntimeError):
                self.config._resolve_runtime_env_file()

    def test_directory_override_fails_closed(self):
        with patch.dict(os.environ, {RUNTIME_VARIABLE: str(BACKEND.resolve())}):
            with self.assertRaises(RuntimeError):
                self.config._resolve_runtime_env_file()

    def test_environment_file_contents_are_not_printed(self):
        with patch.dict(os.environ, {RUNTIME_VARIABLE: str(CONFIG)}), patch.object(
            builtins, "print"
        ) as mocked_print:
            self.config._resolve_runtime_env_file()
        mocked_print.assert_not_called()

    def test_database_url_and_secret_are_not_printed(self):
        source = CONFIG.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(CONFIG))
        print_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ]
        self.assertEqual(print_calls, [])

    def test_import_does_not_manually_parse_env_file(self):
        source = CONFIG.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(CONFIG))
        prohibited_calls = {"open", "read_text", "read_bytes", "load_dotenv", "dotenv_values"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            self.assertNotIn(name, prohibited_calls)


class LauncherContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = LAUNCHER.read_text(encoding="utf-8-sig")
        cls.lower = cls.source.lower()

    def test_launcher_prefers_local_env_file(self):
        local = 'set "agrosat_runtime_env_file=%backend_dir%\\.env"'
        fallback = 'set "agrosat_runtime_env_file=%primary_root%\\backend\\.env"'
        self.assertIn(local, self.lower)
        self.assertIn(fallback, self.lower)
        self.assertLess(self.lower.index(local), self.lower.index(fallback))

    def test_launcher_falls_back_only_to_verified_primary_env_file(self):
        identity_check = 'if /i not "%primary_common%"=="%launcher_common%" goto :identity_error'
        fallback = 'set "agrosat_runtime_env_file=%primary_root%\\backend\\.env"'
        self.assertIn(identity_check, self.lower)
        self.assertIn(fallback, self.lower)
        self.assertLess(self.lower.index(identity_check), self.lower.index(fallback))

    def test_launcher_fails_when_neither_file_exists(self):
        pattern = re.compile(
            r'if not exist "%agrosat_runtime_env_file%" \(\s*'
            r'echo \[error\] runtime configuration file could not be resolved safely\.\s*'
            r'exit /b 1\s*\)',
            re.IGNORECASE,
        )
        self.assertRegex(self.source, pattern)

    def test_launcher_sets_only_the_path_runtime_variable(self):
        names = re.findall(r'set "(AGROSAT_[A-Z0-9_]+)=', self.source, re.IGNORECASE)
        self.assertEqual({name.upper() for name in names}, {RUNTIME_VARIABLE})

    def test_launcher_does_not_copy_or_link_env_file(self):
        env_lines = [line.lower() for line in self.source.splitlines() if ".env" in line.lower()]
        forbidden = ("copy ", "xcopy ", "robocopy ", "mklink ", "new-item ", "junction")
        self.assertFalse(any(token in line for line in env_lines for token in forbidden))

    def test_launcher_does_not_place_env_contents_in_commands(self):
        env_lines = [line.lower() for line in self.source.splitlines() if ".env" in line.lower()]
        forbidden = ("type ", "get-content", "set /p", "for /f")
        self.assertFalse(any(token in line for line in env_lines for token in forbidden))

    def test_frontend_run_id_and_owned_cleanup_contracts_remain(self):
        required = (
            "[guid]::NewGuid().ToString('N')",
            "^[0-9a-f]{32}$",
            ":cleanup_frontend_link",
            "MARKER_RUN_ID",
            "ReparsePoint",
            "if /I not \"%MARKER_TARGET%\"==\"%EXPECTED_TARGET%\"",
        )
        for contract in required:
            with self.subTest(contract=contract):
                self.assertIn(contract, self.source)

    def test_occupied_ports_remain_fail_closed(self):
        self.assertIn("call :require_free_port 8000 || exit /b 1", self.source)
        self.assertIn("call :require_free_port 5173 || exit /b 1", self.source)
        self.assertIn("Get-NetTCPConnection", self.source)

    def test_preflight_starts_no_services_and_creates_no_marker_or_junction(self):
        marker = ROOT / "frontend" / ".agrosat-launcher-node-modules.marker"
        node_modules = ROOT / "frontend" / "node_modules"
        marker_before = marker.exists()
        link_before = node_modules.is_symlink() or (
            node_modules.exists() and bool(node_modules.stat().st_file_attributes & 0x400)
        )
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", str(LAUNCHER), "--preflight"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, "launcher preflight failed")
        self.assertIn("runtime config mode = verified-primary", result.stdout)
        self.assertEqual(marker.exists(), marker_before)
        link_after = node_modules.is_symlink() or (
            node_modules.exists() and bool(node_modules.stat().st_file_attributes & 0x400)
        )
        self.assertEqual(link_after, link_before)
        self.assertNotIn("Starting backend", result.stdout)
        self.assertNotIn("Starting frontend", result.stdout)

    def test_changed_runtime_source_contains_no_operational_credential(self):
        result = subprocess.run(
            ["git", "diff", "--unified=0", "--", "start.bat", "backend/config.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            added = "\n".join(
                line[1:] for line in result.stdout.splitlines() if line.startswith("+") and not line.startswith("+++")
            )
        else:
            added = self.source + "\n" + CONFIG.read_text(encoding="utf-8-sig")
        schemes = ("postgres" + "ql://", "postgres://")
        self.assertFalse(any(scheme in added.lower() for scheme in schemes))
        self.assertNotRegex(added, re.compile(r"secret_key\s*=\s*['\"][^'\"]+", re.IGNORECASE))
        self.assertNotRegex(added, re.compile(r"password\s*=\s*['\"][^'\"]+", re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
