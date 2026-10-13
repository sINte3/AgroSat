"""Regression tests for verified worktree runtime configuration."""

import ast
import builtins
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
CONFIG = BACKEND / "config.py"
LAUNCHER = ROOT / "start.bat"
RUNTIME_VARIABLE = "AGROSAT_RUNTIME_ENV_FILE"

# The launcher's hard-coded dependency root, and the only two outcomes its preflight may have.
PRIMARY_ROOT = Path(r"C:\AgroSat")
PREFLIGHT_PASS = "[PASS] Preflight completed. No services were started and no files were changed."
IDENTITY_REFUSAL = "[ERROR] Repository identity could not be verified."
SERVICE_START_PREFIXES = (
    "Starting backend",
    "Starting frontend",
    "Backend:",
    "Frontend:",
    "Shared frontend dependency junction",
)
SENSITIVE_OUTPUT = re.compile(
    r"postgres(?:ql)?(?:\+\w+)?://|secret_key\s*[=:]|database_url\s*[=:]|password\s*[=:]",
    re.IGNORECASE,
)
# Variables that would redirect Git discovery for the topology probe and the launcher alike.
GIT_DISCOVERY_VARIABLES = (
    "GIT_DIR",
    "GIT_COMMON_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
)
GIT_TIMEOUT_SECONDS = 15
LAUNCHER_TIMEOUT_SECONDS = 60
REPARSE_POINT = 0x400


def _launcher_environment():
    return {name: value for name, value in os.environ.items() if name.upper() not in GIT_DISCOVERY_VARIABLES}


def _git_common_dir(root, env):
    """Absolute, case-normalised Git common directory of root, or None when it cannot be verified."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = result.stdout.splitlines()
    if result.returncode != 0 or len(lines) != 1 or not lines[0].strip():
        return None
    # The launcher compares %%~f-expanded paths case-insensitively; abspath + normcase is the same rule.
    common = os.path.normcase(os.path.abspath(lines[0].strip()))
    return common if os.path.isdir(os.path.join(common, "objects")) else None


def _is_trusted_topology(root, env):
    """Trusted only when root and the primary checkout share one verified repository object store."""
    root_common = _git_common_dir(root, env)
    return root_common is not None and root_common == _git_common_dir(PRIMARY_ROOT, env)


def _run_launcher(launcher, *arguments, env):
    return subprocess.run(
        ["cmd.exe", "/d", "/c", str(launcher), *arguments],
        cwd=launcher.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=LAUNCHER_TIMEOUT_SECONDS,
        # A hidden console of its own, so chcp and title never touch the test runner's console.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _footprint(root):
    """What a launcher run could leave in a checkout: directory listings, marker and node_modules state."""

    def entry(path):
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        return bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT), info.st_mtime_ns

    frontend = root / "frontend"
    listings = {name: sorted(item.name for item in (root / name).iterdir()) for name in (".", "backend", "frontend")}
    return {
        "listings": listings,
        "marker": entry(frontend / ".agrosat-launcher-node-modules.marker"),
        "node_modules": entry(frontend / "node_modules"),
    }


def _same_path(printed, expected):
    if os.path.normcase(os.path.abspath(printed)) == os.path.normcase(os.path.abspath(expected)):
        return True
    try:
        return os.path.samefile(printed, expected)
    except OSError:
        return False


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

    def test_identity_refusal_is_the_launchers_fail_closed_exit_before_any_runtime_resolution(self):
        refusal = re.compile(
            r"^:identity_error\n" + re.escape(f"echo {IDENTITY_REFUSAL}") + r"\nexit /b 1$", re.MULTILINE
        )
        self.assertRegex(self.source, refusal)
        resolution = self.source.index('set "RUNTIME_CONFIG_MODE=local"')
        gates = (
            "if not defined PRIMARY_COMMON goto :identity_error",
            'if /I "%IDENTITY_MODE%"=="git" if /I not "%PRIMARY_COMMON%"=="%LAUNCHER_COMMON%" goto :identity_error',
        )
        for gate in gates:
            with self.subTest(gate=gate):
                self.assertLess(self.source.index(gate), resolution)

    def test_preflight_returns_before_any_port_probe_link_or_service_start(self):
        preflight_exit = re.search(
            r'if /I "%MODE%"=="preflight" \(\s*echo ' + re.escape(PREFLIGHT_PASS) + r"\s*exit /b 0\s*\)",
            self.source,
        )
        self.assertIsNotNone(preflight_exit)
        for side_effect in ("call :require_free_port", "call :create_frontend_link", "mklink", 'start "AgroSat'):
            with self.subTest(side_effect=side_effect):
                self.assertLess(preflight_exit.end(), self.source.index(side_effect))

    def test_preflight_starts_no_services_and_creates_no_marker_or_junction(self):
        # The expectation is fixed from the Git topology before the real launcher runs: only a
        # checkout sharing the primary repository's object store may pass; any other checkout
        # (a clean CI clone, an unrelated local repository) must be refused on identity.
        env = _launcher_environment()
        trusted = _is_trusted_topology(ROOT, env)
        before = _footprint(ROOT)
        result = _run_launcher(LAUNCHER, "--preflight", env=env)
        self._assert_no_startup_or_sensitive_output(result)
        self.assertEqual(_footprint(ROOT), before, "launcher preflight changed the checkout")
        if trusted:
            self._assert_verified_preflight(result, ROOT)
        else:
            self._assert_identity_refusal(result, ROOT)

    def test_preflight_refuses_an_unrelated_checkout(self):
        env = _launcher_environment()
        with tempfile.TemporaryDirectory(prefix="agrosat-preflight-") as parent:
            scratch = self._unrelated_checkout(Path(parent), env)
            launcher = scratch / "start.bat"
            shutil.copyfile(LAUNCHER, launcher)
            self.assertFalse(_is_trusted_topology(scratch, env))
            before = _footprint(scratch)
            result = _run_launcher(launcher, "--preflight", env=env)
            self._assert_no_startup_or_sensitive_output(result)
            self.assertEqual(_footprint(scratch), before, "launcher preflight changed the unrelated checkout")
            self._assert_identity_refusal(result, scratch)
        self.assertFalse(Path(parent).exists())

    def test_preflight_root_refuses_a_source_from_another_repository(self):
        env = _launcher_environment()
        with tempfile.TemporaryDirectory(prefix="agrosat-preflight-") as parent:
            scratch = self._unrelated_checkout(Path(parent), env)
            self.assertNotEqual(_git_common_dir(scratch, env), _git_common_dir(ROOT, env))
            before = (_footprint(ROOT), _footprint(scratch))
            result = _run_launcher(LAUNCHER, "--preflight-root", str(scratch), env=env)
            self._assert_no_startup_or_sensitive_output(result)
            self.assertEqual((_footprint(ROOT), _footprint(scratch)), before, "launcher preflight changed a checkout")
            self._assert_identity_refusal(result, scratch)
        self.assertFalse(Path(parent).exists())

    def _unrelated_checkout(self, parent, env):
        """A brand-new Git repository holding only the two files the launcher's checkout check needs."""
        scratch = parent / "unrelated-checkout"
        (scratch / "backend").mkdir(parents=True)
        (scratch / "frontend").mkdir()
        (scratch / "backend" / "main.py").write_text("", encoding="utf-8")
        (scratch / "frontend" / "package.json").write_text("{}\n", encoding="utf-8")
        init = subprocess.run(
            ["git", "init", "--quiet", str(scratch)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_SECONDS,
        )
        self.assertEqual(init.returncode, 0, init.stderr)
        self.assertIsNotNone(_git_common_dir(scratch, env))
        return scratch

    def _assert_no_startup_or_sensitive_output(self, result):
        # Checked first and reported without echoing the output.
        self.assertFalse(bool(SENSITIVE_OUTPUT.search(result.stdout + result.stderr)), "preflight printed configuration")
        self.assertEqual(result.stderr, "", "launcher preflight wrote to stderr")
        for line in result.stdout.splitlines():
            self.assertFalse(line.strip().startswith(SERVICE_START_PREFIXES), f"preflight reported: {line.strip()}")

    def _assert_verified_preflight(self, result, root):
        """Trusted topology: the real preflight must pass and report truthfully what it resolved."""
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(result.returncode, 0, f"launcher preflight failed in a verified topology: {lines}")
        local_python = root / "backend" / "venv" / "Scripts" / "python.exe"
        local_frontend = root / "frontend" / "node_modules"
        config_mode = "local" if (root / "backend" / ".env").exists() else "verified-primary"
        expected = (
            ("Source checkout root:", root),
            ("Verified source identity: git", None),
            ("Verified dependency root:", PRIMARY_ROOT),
            (f"runtime config mode = {config_mode}", None),
            (
                "Selected Python:",
                local_python if local_python.exists() else PRIMARY_ROOT / "backend" / "venv" / "Scripts" / "python.exe",
            ),
            (
                f"Frontend dependencies: {'local' if local_frontend.is_dir() else 'shared'} from",
                local_frontend if local_frontend.is_dir() else PRIMARY_ROOT / "frontend" / "node_modules",
            ),
            (PREFLIGHT_PASS, None),
        )
        self.assertEqual(len(lines), len(expected), lines)
        for line, (text, path) in zip(lines, expected):
            if path is None:
                self.assertEqual(line, text)
            else:
                self._assert_path_line(line, text, path)

    def _assert_identity_refusal(self, result, root):
        """Untrusted topology: the real preflight must refuse on repository identity and do nothing else."""
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertNotIn(PREFLIGHT_PASS, result.stdout, "launcher preflight passed in an unverified topology")
        self.assertEqual(result.returncode, 1, f"expected the repository-identity refusal: {lines}")
        self.assertEqual(len(lines), 2, lines)
        self._assert_path_line(lines[0], "Source checkout root:", root)
        self.assertEqual(lines[1], IDENTITY_REFUSAL)

    def _assert_path_line(self, line, label, path):
        prefix = f'{label} "'
        self.assertTrue(line.startswith(prefix) and line.endswith('"'), line)
        self.assertTrue(_same_path(line[len(prefix) : -1], path), f"{line} does not name {path}")

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
