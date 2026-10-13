"""TASK_258: the credential ACL probe under an inherited PowerShell 7 module path, fail-closed.

GitHub Actions runs every Windows ``run:`` step in pwsh (PowerShell 7). PowerShell 7 puts
its own module folders at the front of PSModulePath, python inherits that value, and so did
the ``powershell.exe`` the tool starts: Windows PowerShell then resolved Get-Acl to
PowerShell 7's Microsoft.PowerShell.Security, could not load it (its type file redefines
ObjectSecurity members Windows PowerShell already has), and every real ACL read failed.

The real tests use the real powershell.exe, Get-Acl and icacls on disposable directories,
with PSModulePath polluted by a module folder laid out like PowerShell 7's
``Modules\\Microsoft.PowerShell.Security`` (manifest and type file, no assembly beside it).
On a GitHub runner the real PowerShell 7 folders follow it. The fault-injection tests
replace only ``subprocess.run`` inside the tool, in addition to, never instead of, the real
tests here and in test_task238_manage_user_accounts.py.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import manage_user_accounts as tool
from test_task238_manage_user_accounts import current_user_sid, private_directory, read_credential


ME = "S-1-5-21-1000-2000-3000-1001"
VALID = {"protected": True, "user": ME, "rules": [
    {"sid": "S-1-5-18", "allow": True, "inherit_only": False},
    {"sid": "S-1-5-32-544", "allow": True, "inherit_only": False},
    {"sid": "S-1-3-4", "allow": True, "inherit_only": False},
    {"sid": ME, "allow": True, "inherit_only": False},
]}
BROAD_PRINCIPALS = {
    "S-1-1-0": "Everyone",
    "S-1-5-32-545": "BUILTIN\\Users",
    "S-1-5-11": "Authenticated Users",
    "S-1-5-4": "INTERACTIVE",
    "S-1-5-19": "LOCAL SERVICE (an unrelated principal)",
}

SHADOW_MANIFEST = """@{
ModuleVersion = '7.0.0.0'
CompatiblePSEditions = @('Core')
PowerShellVersion = '3.0'
FunctionsToExport = @()
CmdletsToExport = 'Get-Acl', 'Set-Acl'
AliasesToExport = @()
NestedModules = 'Microsoft.PowerShell.Security.dll'
RequiredAssemblies = 'Microsoft.PowerShell.Security.dll'
TypesToProcess = 'Security.types.ps1xml'
}
"""
SHADOW_TYPES = """<?xml version="1.0" encoding="utf-8" ?>
<Types>
  <Type>
    <Name>System.Security.AccessControl.ObjectSecurity</Name>
    <Members>
      <ScriptProperty>
        <Name>AccessToString</Name>
        <GetScriptBlock>''</GetScriptBlock>
      </ScriptProperty>
    </Members>
  </Type>
</Types>
"""


def grant(path: Path, *grants: str) -> None:
    subprocess.run(["icacls", str(path), "/grant", *grants],
                   capture_output=True, text=True, errors="replace", check=True)


def deny(path: Path, *denials: str) -> None:
    subprocess.run(["icacls", str(path), "/deny", *denials],
                   capture_output=True, text=True, errors="replace", check=True)


@unittest.skipUnless(os.name == "nt", "Windows access control lists")
class InheritedPowerShell7ModulePathTests(unittest.TestCase):
    """Real ACLs, read while PSModulePath lists a PowerShell 7 style module folder first."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="t258-acl-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        modules = Path(tempfile.mkdtemp(prefix="t258-ps7-modules-")).resolve()
        self.addCleanup(shutil.rmtree, modules, True)
        security = modules / "Microsoft.PowerShell.Security"
        security.mkdir()
        (security / "Microsoft.PowerShell.Security.psd1").write_text(SHADOW_MANIFEST, encoding="utf-8")
        (security / "Security.types.ps1xml").write_text(SHADOW_TYPES, encoding="utf-8")
        inherited = os.environ.get("PSModulePath", "")
        polluted = patch.dict(os.environ, {"PSModulePath": f"{modules};{inherited}"})
        polluted.start()
        self.addCleanup(polluted.stop)

    def test_the_inherited_module_path_breaks_get_acl_when_passed_on(self):
        # Control: the pollution is effective, so the tests below prove the fix and not the fixture.
        private = private_directory(self.root)
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", tool._ACL_QUERY],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            env=dict(os.environ, **{tool._ACL_TARGET_VARIABLE: str(private)}), stdin=subprocess.DEVNULL,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("CouldNotAutoloadMatchingModule", completed.stderr)

    def test_real_private_directory_is_recognized(self):
        private = private_directory(self.root)
        acl = tool._read_windows_acl(private)
        me = current_user_sid()
        self.assertIs(acl["protected"], True)
        self.assertEqual(acl["user"], me)
        granted = {rule["sid"] for rule in acl["rules"] if rule["allow"]}
        self.assertTrue({"S-1-5-18", "S-1-5-32-544", me} <= granted)
        self.assertTrue(granted <= set(tool._TRUSTED_SIDS) | {me})
        self.assertIsNone(tool._privacy_problem(private, directory=True))

    def test_credential_file_is_created_once_private_unprinted_and_removable(self):
        private = private_directory(self.root)
        target = tool.check_credential_path(str(private / "hand-off.env"))
        self.assertFalse(target.exists())
        secret = tool.generate_password()
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            handle = tool.write_credential_file(target, tool._credential_body(
                "ivanov@agrosat.uz", secret, operation="create", role="agronomist", enterprise_id=9, user_id=None))
        self.assertTrue(handle.written)
        self.assertNotIn(secret, stdout.getvalue() + stderr.getvalue())
        self.assertEqual(read_credential(target), ("ivanov@agrosat.uz", secret))
        self.assertIsNone(tool._privacy_problem(target, directory=False))
        with self.assertRaises(tool.AccountError) as caught:
            tool.write_credential_file(target, "replacement")
        self.assertEqual(caught.exception.code, tool.EXIT_CREDENTIAL_OUTPUT)
        self.assertEqual(read_credential(target), ("ivanov@agrosat.uz", secret))
        self.assertTrue(handle.remove())
        self.assertFalse(target.exists())

    def test_inherited_directory_is_rejected(self):
        # Its inherited entries are all trusted principals: only the protection check refuses it.
        shared = self.root / "shared"
        shared.mkdir()
        acl = tool._read_windows_acl(shared)
        self.assertIs(acl["protected"], False)
        self.assertIn("inherits", tool._privacy_problem(shared, directory=True))
        with self.assertRaises(tool.AccountError) as caught:
            tool.check_credential_path(str(shared / "x.env"))
        self.assertEqual(caught.exception.code, tool.EXIT_CREDENTIAL_OUTPUT)
        self.assertIn("not private", str(caught.exception))
        self.assertFalse((shared / "x.env").exists())

    def test_broad_or_unrelated_principals_are_rejected(self):
        for sid, name in BROAD_PRINCIPALS.items():
            with self.subTest(principal=name):
                widened = private_directory(self.root)
                grant(widened, f"*{sid}:(OI)(CI)R")
                self.assertIn(sid, tool._privacy_problem(widened, directory=True))
                with self.assertRaises(tool.AccountError) as caught:
                    tool.check_credential_path(str(widened / "x.env"))
                self.assertEqual(caught.exception.code, tool.EXIT_CREDENTIAL_OUTPUT)

    def test_a_deny_entry_stays_a_deny_and_grants_nothing(self):
        private = private_directory(self.root)
        deny(private, "*S-1-1-0:(OI)(CI)(W)")
        rules = tool._read_windows_acl(private)["rules"]
        self.assertEqual([rule["allow"] for rule in rules if rule["sid"] == "S-1-1-0"], [False])
        self.assertIsNone(tool._privacy_problem(private, directory=True))
        grant(private, "*S-1-1-0:(OI)(CI)R")
        self.assertIn("S-1-1-0", tool._privacy_problem(private, directory=True))

    def test_a_file_that_inherits_a_broad_grant_is_removed_before_any_secret_is_written(self):
        leaky = private_directory(self.root)
        grant(leaky, "*S-1-5-32-545:(OI)R")
        target = leaky / "hand-off.env"
        with self.assertRaises(tool.AccountError) as caught:
            tool.write_credential_file(target, "AGROSAT_LOGIN_PASSWORD=never-written")
        self.assertEqual(caught.exception.code, tool.EXIT_CREDENTIAL_OUTPUT)
        self.assertIn("S-1-5-32-545", str(caught.exception))
        self.assertFalse(target.exists())


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["powershell.exe"], returncode=returncode, stdout=stdout, stderr=stderr)


class ProbeFaultInjectionTests(unittest.TestCase):
    """Only subprocess.run is replaced: everything the probe can return is refused unless exact."""

    def read(self, *, result=None, error=None):
        run = patch.object(tool.subprocess, "run", return_value=result, side_effect=error)
        with run:
            return tool._read_windows_acl(Path("C:/credential-location"))

    def assert_unreadable(self, stage, **kwargs):
        with self.assertRaises(tool.AccountError) as caught:
            self.read(**kwargs)
        message = str(caught.exception)
        self.assertEqual(caught.exception.code, tool.EXIT_CREDENTIAL_OUTPUT)
        self.assertIn("could not be read", message)
        self.assertIn(f"(stage: {stage})", message)
        self.assertNotIn("S-1-", message)
        self.assertNotIn("credential-location", message)
        return message

    def payload(self, value):
        return completed(stdout=json.dumps(value))

    def test_the_exact_shape_is_accepted(self):
        self.assertEqual(self.read(result=self.payload(VALID)), VALID)
        lone = dict(VALID, rules=VALID["rules"][0])
        self.assertEqual(self.read(result=self.payload(lone))["rules"], [VALID["rules"][0]])
        empty = dict(VALID, rules=[])
        self.assertEqual(self.read(result=self.payload(empty))["rules"], [])
        noisy = completed(stdout="\r\n" + json.dumps(VALID) + "\r\n", stderr="warning")
        self.assertEqual(self.read(result=noisy), VALID)

    def test_windows_powershell_gets_no_inherited_module_path(self):
        target = Path("C:/credential-location")
        with patch.dict(os.environ, {"PSModulePath": "C:\\pwsh7\\Modules", "SystemRoot": "C:\\Windows"}), \
                patch.object(tool.subprocess, "run", return_value=self.payload(VALID)) as run:
            tool._read_windows_acl(target)
        (command,), options = run.call_args
        self.assertEqual(command, ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", tool._ACL_QUERY])
        environment = options["env"]
        self.assertEqual([name for name in environment if name.upper() == "PSMODULEPATH"], [])
        self.assertEqual(environment[tool._ACL_TARGET_VARIABLE], str(target))
        by_name = {name.upper(): value for name, value in environment.items()}
        self.assertEqual(by_name["SYSTEMROOT"], "C:\\Windows")
        self.assertIs(options["stdin"], subprocess.DEVNULL)
        self.assertTrue(options["timeout"])

    def test_a_probe_that_cannot_run_is_unreadable(self):
        for error in (FileNotFoundError(2, "missing"), PermissionError(13, "denied"), OSError(5, "io"),
                      ValueError("embedded null character"), subprocess.SubprocessError()):
            with self.subTest(error=type(error).__name__):
                self.assert_unreadable("start", error=error)
        self.assert_unreadable("timeout", error=subprocess.TimeoutExpired("powershell.exe", 60))

    def test_a_failed_probe_is_never_read_even_with_valid_output(self):
        for code in (1, 255, 3221225781):
            with self.subTest(code=code):
                self.assert_unreadable(f"exit code {code}", result=completed(code, json.dumps(VALID)))

    def test_output_that_is_not_json_is_unreadable(self):
        text = json.dumps(VALID)
        for stdout in ("", "  \r\n", "not json", text[:-1], "\ufeff" + text, text + "\nnoise", "noise\n" + text):
            with self.subTest(stdout=stdout[:12]):
                self.assert_unreadable("output", result=completed(stdout=stdout))

    def test_scalar_array_and_incomplete_payloads_are_unreadable(self):
        broken = [True, 1, "x", None, [], [VALID], {}]
        broken += [{key: value for key, value in VALID.items() if key != missing} for missing in VALID]
        broken.append(dict(VALID, extra=1))
        broken += [dict(VALID, protected=value) for value in ("False", "true", 0, 1, None)]
        broken += [dict(VALID, user=value) for value in (None, 7, "", "Everyone", "S-1-5", ME + " ", [ME])]
        broken += [dict(VALID, rules=value) for value in (None, "x", 5, {}, [1], [None], [[]], [VALID["rules"]])]
        rule = VALID["rules"][0]
        broken += [dict(VALID, rules=[{key: value for key, value in rule.items() if key != missing}])
                   for missing in rule]
        broken.append(dict(VALID, rules=[dict(rule, extra=True)]))
        broken += [dict(VALID, rules=[dict(rule, sid=value)]) for value in (5, None, "", "Everyone", "S-1")]
        broken += [dict(VALID, rules=[dict(rule, allow=value)]) for value in ("yes", "false", 1, 0, None)]
        broken += [dict(VALID, rules=[dict(rule, inherit_only=value)]) for value in ("no", 0, 1, None)]
        for value in broken:
            with self.subTest(payload=json.dumps(value)[:80]):
                self.assert_unreadable("payload", result=self.payload(value))

    @unittest.skipUnless(os.name == "nt", "the Windows privacy check reads the ACL")
    def test_an_unreadable_acl_refuses_the_location_and_removes_the_new_file(self):
        root = Path(tempfile.mkdtemp(prefix="t258-fault-")).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        target = root / "hand-off.env"
        for failure in ({"result": completed(1)}, {"result": completed(stdout="{}")},
                        {"error": FileNotFoundError(2, "missing")}):
            with self.subTest(failure=failure), patch.object(tool.subprocess, "run", return_value=failure.get("result"),
                                                             side_effect=failure.get("error")):
                with self.assertRaises(tool.AccountError) as caught:
                    tool.check_credential_path(str(target))
                self.assertEqual(caught.exception.code, tool.EXIT_CREDENTIAL_OUTPUT)
                with self.assertRaises(tool.AccountError) as caught:
                    tool.write_credential_file(target, "AGROSAT_LOGIN_PASSWORD=never-written")
                self.assertEqual(caught.exception.code, tool.EXIT_CREDENTIAL_OUTPUT)
                self.assertFalse(target.exists())


class SecurityContractTests(unittest.TestCase):
    def test_trusted_principals_are_not_broadened(self):
        self.assertEqual(tool._TRUSTED_SIDS, frozenset({"S-1-5-18", "S-1-5-32-544", "S-1-3-4"}))
        self.assertEqual(tool._CREATOR_OWNER_SID, "S-1-3-0")

    def test_the_query_still_reads_the_dacl_by_sid_with_get_acl_and_stops_on_error(self):
        for fragment in ("$ErrorActionPreference = 'Stop'", "Get-Acl -LiteralPath $env:AGROSAT_CREDENTIAL_ACL_TARGET",
                         "GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])",
                         "AreAccessRulesProtected", "[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value",
                         "ConvertTo-Json -Compress"):
            self.assertIn(fragment, tool._ACL_QUERY)


if __name__ == "__main__":
    unittest.main()
