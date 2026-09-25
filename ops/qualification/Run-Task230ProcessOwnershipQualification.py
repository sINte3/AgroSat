"""TASK_230 real Windows process-tree ownership qualification.

Registers temporary ``\\AgroSat_TASK230_*`` Scheduled Tasks (SYSTEM, no
trigger), runs probe releases on spare loopback ports, stops each task with
``Stop-ScheduledTask`` and records which processes and listeners survive.

Nothing here terminates a process: no ``taskkill``, no ``Stop-Process``, no
``TerminateProcess``. Processes are identified only by lineage below the
Task Scheduler engine process of a TASK_230 task (PID plus creation time), and
every probe process ends by itself after a bounded lifetime, so a scenario
that demonstrates orphans cleans up without a kill.

Scenarios (``--scenario``; default all):

* ``app-*-owned``: the TASK_230 launcher (Job Object ownership).
* ``app-*-legacy``: the 448f404 launcher with only its hardcoded production
  roots and ports replaced by the probe's (the "before" measurement).
* ``worker-*-current``: the real collector and notification runners, running
  probe interpreters shaped like the canonical workers (Part C).
* ``worker-collector-current-detached-provider``: diagnostic; the provider
  child gets its own console, to show what ends the canonical worker tree.
* ``second-launcher``, ``invalid-identity``, ``child-exit-recovery``.

Every scenario writes one JSON evidence file; ``summary.json`` lists them all.
Run elevated. Never touches a production task, port, root or database.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

REPOSITORY = Path(__file__).resolve().parents[2]
BASE_SHA = "448f40407ffcdbf635a5cc7816fcab484dd80a5b"
TASK_PREFIX = "AgroSat_TASK230_"
FORBIDDEN_PORTS = {8000, 5173}
PORT_RANGE = range(58230, 58330)
REHEARSAL_BASE = Path(r"C:\AgroSat_rehearsal\TASK_230\ownership")
SIGNING_THUMBPRINT = "816767BE400FE53327432B12B29FE4B5809CA4CA"
NODE = Path(r"C:\Program Files\nodejs\node.exe")
BASE_PYTHON = Path(r"C:\Program Files\Python314\python.exe")
PROBE_PACKAGES = Path(r"C:\AgroSat\backend\venv\Lib\site-packages")
STOP_DEADLINE_SECONDS = 30
LEGACY_TTL_SECONDS = 75
OWNED_TTL_SECONDS = 900


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def q(value: object) -> str:
    """A PowerShell single-quoted literal."""
    return "'" + str(value).replace("'", "''") + "'"


def powershell(script: str, *, check: bool = True, timeout: int = 180) -> str:
    # Under 'Stop' every error terminates the script with exit code 1; reaching
    # the end means success, whatever a suppressed lookup left in $?.
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; "
         + script + "\nexit 0"],
        capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(f"POWERSHELL_FAILED:{result.returncode}:{script[:160]}:"
                           f"{(result.stderr or result.stdout).strip()[:600]}")
    return result.stdout.strip()


def ps_json(script: str):
    output = powershell(script)
    return json.loads(output) if output else None


def assert_task_name(name: str) -> None:
    if not name.startswith(TASK_PREFIX) or "\\" in name:
        raise RuntimeError("TASK230_TASK_IDENTITY_REJECTED")


def port_free(port: int) -> bool:
    if port in FORBIDDEN_PORTS:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


class Ports:
    def __init__(self):
        self._used: set[int] = set()

    def take(self) -> int:
        for port in PORT_RANGE:
            if port not in self._used and port_free(port):
                self._used.add(port)
                return port
        raise RuntimeError("TASK230_NO_SPARE_PORT")


def listeners(ports: list[int]) -> dict[int, int | None]:
    rows = ps_json(
        "@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
        f"Where-Object {{ @({','.join(map(str, ports))}) -contains $_.LocalPort }} | "
        "Select-Object LocalPort,OwningProcess) | ConvertTo-Json -Compress") or []
    if isinstance(rows, dict):
        rows = [rows]
    found = {int(row["LocalPort"]): int(row["OwningProcess"]) for row in rows}
    return {port: found.get(port) for port in ports}


def process_table() -> dict[int, dict]:
    rows = ps_json(
        "@(Get-CimInstance Win32_Process | ForEach-Object { [pscustomobject]@{ pid=[int]$_.ProcessId; "
        "ppid=[int]$_.ParentProcessId; name=[string]$_.Name; "
        "created=$(if ($_.CreationDate) { $_.CreationDate.ToUniversalTime().ToString('o') } else { '' }) } }) "
        "| ConvertTo-Json -Compress")
    return {int(row["pid"]): row for row in rows}


def lineage(table: dict[int, dict], root: int) -> list[dict]:
    """Every process below ``root``, following parent links whose parent predates the child."""
    found, frontier = [], [root]
    while frontier:
        parent = frontier.pop()
        for pid, row in table.items():
            if row["ppid"] == parent and pid != parent and pid not in {item["pid"] for item in found}:
                if table.get(parent, {}).get("created", "") <= row["created"]:
                    found.append(row)
                    frontier.append(pid)
    return found


def alive(rows: list[dict]) -> list[dict]:
    table = process_table()
    return [row for row in rows if table.get(row["pid"], {}).get("created") == row["created"]]


def engine_pids(name: str) -> list[int]:
    assert_task_name(name)
    output = powershell(
        "$s = New-Object -ComObject Schedule.Service; $s.Connect(); "
        f"$t = $s.GetFolder('\\').GetTask({q(name)}); "
        "@($t.GetInstances(0) | ForEach-Object { [int]$_.EnginePID }) -join ','")
    return [int(item) for item in output.split(",") if item.strip()]


def task_state(name: str) -> dict:
    assert_task_name(name)
    return ps_json(
        f"$t = Get-ScheduledTask -TaskPath '\\' -TaskName {q(name)}; "
        f"$i = Get-ScheduledTaskInfo -TaskPath '\\' -TaskName {q(name)}; "
        "[pscustomobject]@{ state=[string]$t.State; last_result=[int64]$i.LastTaskResult; "
        "last_run=$i.LastRunTime.ToString('o') } | ConvertTo-Json -Compress")


def register_task(name: str, execute: Path, arguments: str, working_directory: Path, *,
                  restart_count: int = 3, restart_minutes: int = 1) -> None:
    assert_task_name(name)
    unregister_task(name)
    powershell(
        f"$a = New-ScheduledTaskAction -Execute {q(execute)} -Argument {q(arguments)} "
        f"-WorkingDirectory {q(working_directory)}; "
        "$p = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest; "
        "$s = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) "
        f"-RestartCount {restart_count} -RestartInterval (New-TimeSpan -Minutes {restart_minutes}) "
        "-StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
        f"Register-ScheduledTask -TaskPath '\\' -TaskName {q(name)} -Action $a -Principal $p -Settings $s "
        "-Description 'TASK_230 temporary process ownership qualification; no trigger' | Out-Null")


def start_task(name: str) -> None:
    assert_task_name(name)
    powershell(f"Start-ScheduledTask -TaskPath '\\' -TaskName {q(name)}")


def stop_task(name: str) -> None:
    assert_task_name(name)
    powershell(f"Stop-ScheduledTask -TaskPath '\\' -TaskName {q(name)}")


def unregister_task(name: str) -> None:
    assert_task_name(name)
    powershell(
        f"$t = Get-ScheduledTask -TaskPath '\\' -TaskName {q(name)} -ErrorAction SilentlyContinue; "
        f"if ($t) {{ if ([string]$t.State -eq 'Running') {{ Stop-ScheduledTask -TaskPath '\\' -TaskName {q(name)} }}; "
        f"Unregister-ScheduledTask -TaskPath '\\' -TaskName {q(name)} -Confirm:$false }}")


def wait_until(predicate, deadline_seconds: float, interval: float = 0.25):
    started = time.monotonic()
    while True:
        value = predicate()
        if value:
            return True, round(time.monotonic() - started, 3), value
        if time.monotonic() - started >= deadline_seconds:
            return False, round(time.monotonic() - started, 3), value
        time.sleep(interval)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_show(path: str) -> str:
    return subprocess.run(["git", "-C", str(REPOSITORY), "show", f"{BASE_SHA}:{path}"],
                          capture_output=True, text=True, check=True, encoding="utf-8").stdout


def sign(paths: list[Path]) -> None:
    joined = ",".join(q(path) for path in paths)
    powershell(
        f"$c = Get-Item Cert:\\LocalMachine\\My\\{SIGNING_THUMBPRINT}; "
        f"foreach ($f in @({joined})) {{ $r = Set-AuthenticodeSignature -FilePath $f -Certificate $c -HashAlgorithm SHA256; "
        "if ($r.Status -ne 'Valid') { throw ('SIGNING_FAILED:' + $f) } }")


BACKEND_PROBE = r'''"""TASK_230 ownership probe backend (not AgroSat code)."""
import json, os, subprocess, sys, threading
from pathlib import Path
HERE = Path(__file__).resolve().parent
MODE = json.loads((HERE / "probe-mode.json").read_text(encoding="utf-8"))
threading.Timer(MODE["ttl_seconds"], lambda: os._exit(0)).start()
if MODE.get("crash_after_seconds"):
    threading.Timer(MODE["crash_after_seconds"], lambda: os._exit(3)).start()
if MODE.get("descendant_port"):
    subprocess.Popen([sys.executable, str(HERE / "probe_descendant.py"), str(MODE["descendant_port"]),
                      str(MODE["ttl_seconds"])], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)

async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    body = json.dumps({"status": "alive", "release_revision": MODE["release"]}).encode()
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})
'''

PYTHON_DESCENDANT = r'''"""TASK_230 ownership probe descendant: listens until its lifetime ends."""
import os, socket, sys, threading
port, ttl = int(sys.argv[1]), float(sys.argv[2])
threading.Timer(ttl, lambda: os._exit(0)).start()
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(("127.0.0.1", port))
server.listen(8)
while True:
    connection, _ = server.accept()
    connection.close()
'''

FRONTEND_PROBE = r'''// TASK_230 ownership probe frontend (not AgroSat code).
import http from 'node:http';
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const here = path.dirname(fileURLToPath(import.meta.url));
const mode = JSON.parse(readFileSync(path.join(here, 'probe-mode.json'), 'utf8'));
setTimeout(() => process.exit(0), mode.ttl_seconds * 1000);
const port = Number(process.env.QUALIFICATION_FRONTEND_PORT);
http.createServer((request, response) => { response.end('probe'); }).listen(port, '127.0.0.1');
if (mode.descendant_port) {
  spawn(process.execPath, [path.join(here, 'probe_descendant.mjs'), String(mode.descendant_port), String(mode.ttl_seconds)],
        { stdio: 'ignore' });
}
'''

NODE_DESCENDANT = r'''// TASK_230 ownership probe descendant: listens until its lifetime ends.
import net from 'node:net';
const [port, ttl] = process.argv.slice(2).map(Number);
setTimeout(() => process.exit(0), ttl * 1000);
net.createServer((socket) => socket.end()).listen(port, '127.0.0.1');
'''

WORKER_PROBE = r'''"""TASK_230 worker probe standing in for a canonical worker script (not AgroSat code).

Starts its provider child the way backend/scripts/collect_satellite.py does
(subprocess.Popen, no creation flags: the child shares the task console), or,
for the diagnostic, with CREATE_NO_WINDOW (its own console).
"""
import json, os, socket, subprocess, sys, threading, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
MODE = json.loads((HERE / "probe-mode.json").read_text(encoding="utf-8"))[Path(__file__).stem]
threading.Timer(MODE["ttl_seconds"], lambda: os._exit(0)).start()
subprocess.Popen([sys.executable, str(HERE / "probe_descendant.py"), str(MODE["provider_port"]),
                  str(MODE["ttl_seconds"])], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, creationflags=0x08000000 if MODE.get("detach_provider") else 0)
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(("127.0.0.1", MODE["listen_port"]))
server.listen(8)
while True:
    connection, _ = server.accept()
    connection.close()
'''


class Fixture:
    """One probe release, runtime and runner set below the TASK_230 rehearsal base."""

    def __init__(self, run_root: Path, scenario: str):
        self.root = run_root / scenario
        if self.root.exists():
            raise RuntimeError("TASK230_FIXTURE_EXISTS")
        self.commit = hashlib.sha1(f"TASK230-ownership-{run_root.name}-{scenario}".encode()).hexdigest()
        self.releases = self.root / "releases"
        self.release = self.releases / self.commit
        self.runtimes = self.root / "runtime"
        self.application = self.runtimes / self.commit / "application"
        self.env_file = self.root / "env" / "rehearsal.env"
        for directory in (self.release / "backend" / "scripts", self.release / "frontend" / "dist",
                          self.release / "ops" / "qualification", self.application, self.env_file.parent):
            directory.mkdir(parents=True)
        # A real venv (so the interpreter is the venv launcher, as in production)
        # that imports uvicorn from the backend venv's packages: SYSTEM cannot
        # see a per-user site directory.
        venv = self.release / "backend" / "venv"
        subprocess.run([str(BASE_PYTHON), "-m", "venv", "--without-pip", str(venv)],
                       check=True, capture_output=True)
        (venv / "Lib" / "site-packages" / "task230_probe_packages.pth").write_text(
            str(PROBE_PACKAGES) + "\n", encoding="utf-8")
        (self.release / "backend" / "main.py").write_text(BACKEND_PROBE, encoding="utf-8")
        (self.release / "backend" / "probe_descendant.py").write_text(PYTHON_DESCENDANT, encoding="utf-8")
        for name in ("collect_satellite.py", "reconcile_operational_notifications.py"):
            (self.release / "backend" / "scripts" / name).write_text(WORKER_PROBE, encoding="utf-8")
        (self.release / "backend" / "scripts" / "probe_descendant.py").write_text(PYTHON_DESCENDANT, encoding="utf-8")
        (self.release / "frontend" / "dist" / "index.html").write_text("<!doctype html><title>probe</title>", encoding="utf-8")
        qualification = self.release / "ops" / "qualification"
        (qualification / "Serve-ProgramR1QualificationFrontend.mjs").write_text(FRONTEND_PROBE, encoding="utf-8")
        (qualification / "probe_descendant.mjs").write_text(NODE_DESCENDANT, encoding="utf-8")
        manifest = self.release / "release-manifest.json"
        manifest.write_text(json.dumps({"schema_version": 2, "git_sha": self.commit, "purpose": "TASK_230 ownership probe"}),
                            encoding="utf-8")
        self.manifest_sha256 = sha256(manifest)
        self.env_file.write_text("# TASK_230 probe runtime env: no credentials\n", encoding="utf-8")
        self.python = self.release / "backend" / "venv" / "Scripts" / "python.exe"

    def mode(self, backend: dict | None = None, frontend: dict | None = None, workers: dict | None = None) -> None:
        if backend is not None:
            (self.release / "backend" / "probe-mode.json").write_text(
                json.dumps({"release": self.commit, **backend}), encoding="utf-8")
        if frontend is not None:
            (self.release / "ops" / "qualification" / "probe-mode.json").write_text(json.dumps(frontend), encoding="utf-8")
        if workers is not None:
            (self.release / "backend" / "scripts" / "probe-mode.json").write_text(json.dumps(workers), encoding="utf-8")

    def owned_launcher(self, backend_port: int, frontend_port: int, *, manifest_sha256: str | None = None) -> Path:
        for name in ("Run-AgroSatApplication.py", "agrosat_process_ownership.py"):
            shutil.copyfile(REPOSITORY / "ops" / "release" / name, self.application / name)
        config = {
            "schema_version": 2, "release_commit": self.commit,
            "manifest_sha256": manifest_sha256 or self.manifest_sha256,
            "wrapper_sha256": sha256(self.application / "Run-AgroSatApplication.py"),
            "ownership_sha256": sha256(self.application / "agrosat_process_ownership.py"),
            "runtime_directory": str(self.application), "profile": "rehearsal",
            "rehearsal": {"release_root": str(self.releases), "runtime_root": str(self.runtimes),
                          "runtime_env_file": str(self.env_file), "backend_port": backend_port,
                          "frontend_port": frontend_port, "node_executable": str(NODE)},
        }
        path = self.application / "application-release.json"
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return path

    def legacy_launcher(self, backend_port: int, frontend_port: int) -> Path:
        source = git_show("ops/release/Run-AgroSatApplication.py")
        replacements = [
            ('RELEASE_ROOT = Path(r"C:\\AgroSat_releases\\PROGRAM_R3")', f'RELEASE_ROOT = Path(r"{self.releases}")'),
            ('RUNTIME_ROOT = Path(r"C:\\AgroSat_runtime\\PROGRAM_R3")', f'RUNTIME_ROOT = Path(r"{self.runtimes}")'),
            ('RUNTIME_ENV = Path(r"C:\\AgroSat\\backend\\.env")', f'RUNTIME_ENV = Path(r"{self.env_file}")'),
            ('assert_port_free(8000 if component == "backend" else 5173)',
             f'assert_port_free({backend_port} if component == "backend" else {frontend_port})'),
            ('"--host", "127.0.0.1", "--port", "8000"', f'"--host", "127.0.0.1", "--port", "{backend_port}"'),
            ('{"QUALIFICATION_FRONTEND_PORT": "5173",', f'{{"QUALIFICATION_FRONTEND_PORT": "{frontend_port}",'),
            ('"QUALIFICATION_BACKEND_PORT": "8000",', f'"QUALIFICATION_BACKEND_PORT": "{backend_port}",'),
        ]
        for old, new in replacements:
            if source.count(old) != 1:
                raise RuntimeError("TASK230_LEGACY_LAUNCHER_SHAPE_CHANGED")
            source = source.replace(old, new)
        launcher = self.application / "Run-AgroSatApplication.py"
        launcher.write_text(source, encoding="utf-8")
        config = {"release_commit": self.commit, "manifest_sha256": self.manifest_sha256,
                  "wrapper_sha256": sha256(launcher), "runtime_directory": str(self.application)}
        path = self.application / "application-release.json"
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return path

    def launcher_arguments(self, config: Path, component: str) -> str:
        return (f'-B "{self.application / "Run-AgroSatApplication.py"}" --configuration "{config}" '
                f"--component {component}")

    def lifecycle(self, component: str) -> list[dict]:
        path = self.application / f"{component}-lifecycle.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def in_any_job(pid: int) -> bool | None:
    """Whether a process belongs to any Job Object (read-only query)."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        result = wintypes.BOOL(False)
        return bool(result.value) if kernel32.IsProcessInJob(handle, None, ctypes.byref(result)) else None
    finally:
        kernel32.CloseHandle(handle)


def observe_running(name: str, ports: list[int]) -> dict:
    ok, waited, _ = wait_until(lambda: all(listeners(ports).values()), 90, 0.5)
    if not ok:
        raise RuntimeError(f"TASK230_PROBE_NOT_LISTENING:{listeners(ports)}")
    engines = engine_pids(name)
    if len(engines) != 1:
        raise RuntimeError("TASK230_ENGINE_PID_UNRESOLVED")
    table = process_table()
    tree = lineage(table, engines[0])
    for row in tree:
        row["in_any_job"] = in_any_job(row["pid"])
    root = dict(table[engines[0]], in_any_job=in_any_job(engines[0]))
    owners = listeners(ports)
    tree_pids = {row["pid"] for row in tree} | {engines[0]}
    return {
        "engine": root, "tree": tree, "listener_owner_pids": owners,
        "listeners_owned_by_task_lineage": all(pid in tree_pids for pid in owners.values()),
        "listening_after_seconds": waited,
    }


def stop_and_verify(name: str, running: dict, ports: list[int], *, expect_clean: bool) -> dict:
    processes = [running["engine"], *running["tree"]]
    stopped_at = utc()
    started = time.monotonic()
    stop_task(name)
    clean, elapsed, _ = wait_until(
        lambda: not alive(processes) and not any(listeners(ports).values()), STOP_DEADLINE_SECONDS)
    survivors = alive(processes)
    held = {port: pid for port, pid in listeners(ports).items() if pid}
    result = {
        "stopped_at": stopped_at, "stop_method": "Stop-ScheduledTask",
        "process_kill_commands_used": [], "tree_terminated": clean,
        "seconds_to_clean": elapsed if clean else None,
        "survivors_after_deadline": survivors, "ports_held_after_deadline": held,
        "task_after_stop": task_state(name),
    }
    if not expect_clean and survivors:
        # Orphans end by themselves (bounded probe lifetime); nothing is killed.
        gone, waited, _ = wait_until(lambda: not alive(survivors) and not any(listeners(ports).values()),
                                     LEGACY_TTL_SECONDS + 90, 1.0)
        result["orphans_self_expired"] = gone
        result["orphan_self_expiry_seconds"] = round(time.monotonic() - started, 1)
    return result


def scenario_app(run_root: Path, ports: Ports, *, component: str, descendant: bool, legacy: bool) -> dict:
    name = f"{TASK_PREFIX}{'Legacy' if legacy else 'Owned'}{component.title()}{'Tree' if descendant else ''}"
    scenario = f"app-{component}{'-descendant' if descendant else ''}-{'legacy' if legacy else 'owned'}"
    fixture = Fixture(run_root, scenario)
    backend_port, frontend_port, descendant_port = ports.take(), ports.take(), ports.take()
    ttl = LEGACY_TTL_SECONDS if legacy else OWNED_TTL_SECONDS
    mode = {"ttl_seconds": ttl, "descendant_port": descendant_port if descendant else None}
    fixture.mode(backend=mode if component == "backend" else {"ttl_seconds": ttl},
                 frontend=mode if component == "frontend" else {"ttl_seconds": ttl})
    config = (fixture.legacy_launcher if legacy else fixture.owned_launcher)(backend_port, frontend_port)
    watched = [backend_port if component == "backend" else frontend_port] + ([descendant_port] if descendant else [])
    register_task(name, fixture.python, fixture.launcher_arguments(config, component), fixture.release)
    try:
        start_task(name)
        running = observe_running(name, watched)
        lifecycle = fixture.lifecycle(component)
        members = next((item["ownership"]["member_pids"] for item in lifecycle
                        if item.get("status") == "listening" and "ownership" in item), None)
        stopped = stop_and_verify(name, running, watched, expect_clean=not legacy)
    finally:
        unregister_task(name)
    expected = not legacy
    return {
        "scenario": scenario, "task": "\\" + name, "launcher": "448f404 (constants patched)" if legacy else "TASK_230",
        "ports": {"component": watched[0], "descendant": descendant_port if descendant else None},
        "running": running,
        "job_member_pids_at_listen": members,
        "listener_pids_are_job_members": None if members is None else all(
            pid in members for pid in running["listener_owner_pids"].values()),
        "stop": stopped, "lifecycle": fixture.lifecycle(component),
        "pass": stopped["tree_terminated"] == expected and (legacy or running["listeners_owned_by_task_lineage"]),
        "interpretation": ("orphans survived the task stop (defect reproduced)" if legacy and not stopped["tree_terminated"]
                           else "whole owned tree ended with the task" if stopped["tree_terminated"] else "UNEXPECTED"),
    }


def worker_config(fixture: Fixture, kind: str, runner_dir: Path, *, rehearsal_identity: bool) -> Path:
    if kind == "collector":
        config = {
            "schema_version": 1,
            "task_name": "\\AgroSat_TASK230_SentinelProbe" if rehearsal_identity else "\\AgroSat_PROGRAM_R3_SentinelCycle",
            "task_description": "TASK_230 worker ownership probe",
            "execution_identity": "NT AUTHORITY\\SYSTEM", "execution_sid": "S-1-5-18",
            "expected_windows_timezone_id": "West Asia Standard Time", "release_commit": fixture.commit,
            "immutable_release_root": str(fixture.releases), "release_manifest_sha256": fixture.manifest_sha256,
            "working_directory": str(fixture.release), "python_executable": str(fixture.python),
            "runtime_env_file": str(fixture.env_file), "runner_script": str(runner_dir / "Invoke-Collector.ps1"),
            "schedule": {"daily_at_local_times": ["06:00:00"], "start_when_available": True, "restart_count": 3,
                         "restart_interval_minutes": 15, "execution_time_limit_hours": 6,
                         "multiple_instances": "IgnoreNew"},
            "collector": {"indices": "ndvi", "batch_size": 25, "lookback_days": 14, "max_attempts": 2,
                          "retry_base_seconds": 2, "field_timeout_seconds": 180, "cycle_timeout_seconds": 21600,
                          "output_directory": str(fixture.runtimes / fixture.commit / "collector" / "runs"),
                          "state_directory": str(fixture.runtimes / fixture.commit / "collector" / "state"),
                          "lock_directory": str(fixture.runtimes / fixture.commit / "collector" / "locks")},
        }
    else:
        config = {
            "schema_version": 1,
            "task_name": ("\\AgroSat_TASK230_NotificationsProbe" if rehearsal_identity
                          else "\\AgroSat_PROGRAM_R3_OperationalNotifications"),
            "task_description": "TASK_230 worker ownership probe",
            "execution_identity": "NT AUTHORITY\\SYSTEM", "execution_sid": "S-1-5-18",
            "expected_windows_timezone_id": "West Asia Standard Time", "release_commit": fixture.commit,
            "immutable_release_root": str(fixture.releases), "release_manifest_sha256": fixture.manifest_sha256,
            "working_directory": str(fixture.release), "python_executable": str(fixture.python),
            "runtime_env_file": str(fixture.env_file),
            "runner_script": str(runner_dir / "Invoke-OperationalNotifications.ps1"),
            "schedule": {"interval_minutes": 15, "start_when_available": True, "restart_count": 3,
                         "restart_interval_minutes": 5, "execution_time_limit_minutes": 10,
                         "multiple_instances": "IgnoreNew"},
            "reconciliation": {"limit": 200, "output_directory": str(fixture.runtimes / fixture.commit / "operational-notifications")},
        }
    path = runner_dir / f"{kind}-task.json"
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


WORKER_FILES = {
    "collector": ("Invoke-Collector.ps1", "Task209-CollectorTask.Common.ps1"),
    "notifications": ("Invoke-OperationalNotifications.ps1", "Task221-OperationalNotifications.Common.ps1"),
}


def scenario_worker(run_root: Path, ports: Ports, *, kind: str, detach_provider: bool = False) -> dict:
    """The real worker runners (unchanged by TASK_230) around probe interpreters.

    With ``detach_provider`` the provider child gets its own console: a
    diagnostic showing what ends the canonical worker tree on a task stop.
    """
    scenario = f"worker-{kind}-current{'-detached-provider' if detach_provider else ''}"
    name = f"{TASK_PREFIX}Current{kind.title()}Worker{'Detached' if detach_provider else ''}"
    fixture = Fixture(run_root, scenario)
    runner_dir = fixture.runtimes / "scheduler"
    runner_dir.mkdir(parents=True)
    names = list(WORKER_FILES[kind])
    for file_name in names:
        (runner_dir / file_name).write_text(
            (REPOSITORY / "ops" / "windows-task" / file_name).read_text(encoding="utf-8"), encoding="utf-8")
    listen_port, provider_port = ports.take(), ports.take()
    stem = "collect_satellite" if kind == "collector" else "reconcile_operational_notifications"
    fixture.mode(workers={stem: {"listen_port": listen_port, "provider_port": provider_port,
                                 "ttl_seconds": LEGACY_TTL_SECONDS, "detach_provider": detach_provider}})
    config = worker_config(fixture, kind, runner_dir, rehearsal_identity=True)
    sign([runner_dir / file_name for file_name in names])
    runner = runner_dir / names[0]
    arguments = (f'-NoProfile -NonInteractive -ExecutionPolicy AllSigned -File "{runner}" '
                 f'-ConfigurationPath "{config}" -Mode apply')
    register_task(name, Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"), arguments, fixture.release)
    watched = [listen_port, provider_port]
    try:
        start_task(name)
        running = observe_running(name, watched)
        stopped = stop_and_verify(name, running, watched, expect_clean=not detach_provider)
    finally:
        unregister_task(name)
    provider_pid = running["listener_owner_pids"][provider_port]
    worker_pid = running["listener_owner_pids"][listen_port]
    survivors = {row["pid"] for row in stopped["survivors_after_deadline"]}
    if detach_provider:
        passed = provider_pid in survivors and worker_pid not in survivors
        interpretation = ("only the provider child with its own console survived: the canonical worker tree "
                          "ends on a task stop because every process shares the task console"
                          if passed else "UNEXPECTED")
    else:
        passed = stopped["tree_terminated"]
        interpretation = ("the whole worker tree (worker Python and its provider child) ended with the task: "
                          "no orphan defect" if passed else "worker processes survived the task stop (defect)")
    return {
        "scenario": scenario, "task": "\\" + name,
        "runner": "ops/windows-task runner unchanged by TASK_230 ownership work (TASK_230 Common validators)",
        "execution_policy": "AllSigned (probe copies signed with the scheduler code-signing certificate)",
        "provider_creation": "CREATE_NO_WINDOW (own console)" if detach_provider else "subprocess.Popen default (shared task console)",
        "ports": {"worker": listen_port, "provider_child": provider_port},
        "running": running, "stop": stopped, "pass": passed, "interpretation": interpretation,
    }


def scenario_second_launcher(run_root: Path, ports: Ports) -> dict:
    fixture = Fixture(run_root, "second-launcher")
    backend_port, frontend_port = ports.take(), ports.take()
    fixture.mode(backend={"ttl_seconds": OWNED_TTL_SECONDS}, frontend={"ttl_seconds": OWNED_TTL_SECONDS})
    config = fixture.owned_launcher(backend_port, frontend_port)
    first, second = f"{TASK_PREFIX}FirstBackend", f"{TASK_PREFIX}SecondBackend"
    arguments = fixture.launcher_arguments(config, "backend")
    register_task(first, fixture.python, arguments, fixture.release)
    register_task(second, fixture.python, arguments, fixture.release)
    try:
        start_task(first)
        running = observe_running(first, [backend_port])
        owner_before = listeners([backend_port])[backend_port]
        start_task(first)  # IgnoreNew: a second instance of the same task must not start.
        time.sleep(3)
        instances_same_task = len(engine_pids(first))
        start_task(second)
        finished, waited, _ = wait_until(lambda: task_state(second)["state"] != "Running", 60, 0.5)
        second_state = task_state(second)
        direct = subprocess.run([str(fixture.python), "-B", str(fixture.application / "Run-AgroSatApplication.py"),
                                 "--configuration", str(config), "--component", "backend"],
                                capture_output=True, text=True, timeout=60)
        owner_after = listeners([backend_port])[backend_port]
        started_events = [item for item in fixture.lifecycle("backend") if item.get("status") == "started"]
        stopped = stop_and_verify(first, running, [backend_port], expect_clean=True)
    finally:
        unregister_task(first)
        unregister_task(second)
    direct_report = json.loads(direct.stdout.strip().splitlines()[-1]) if direct.stdout.strip() else None
    return {
        "scenario": "second-launcher", "port": backend_port,
        "same_task_instances_after_second_start": instances_same_task,
        "second_task_finished": finished, "second_task_result": second_state["last_result"],
        "direct_second_launcher_exit_code": direct.returncode, "direct_second_launcher_report": direct_report,
        "listener_owner_unchanged": owner_before == owner_after and owner_before is not None,
        "started_events": len(started_events), "stop": stopped,
        "pass": (instances_same_task == 1 and finished and second_state["last_result"] == 70
                 and direct.returncode == 70 and direct_report is not None
                 and direct_report.get("error_code") == "APPLICATION_OWNERSHIP_LOCK_HELD"
                 and owner_before == owner_after and len(started_events) == 1 and stopped["tree_terminated"]),
    }


def scenario_invalid_identity(run_root: Path, ports: Ports) -> dict:
    fixture = Fixture(run_root, "invalid-identity")
    backend_port, frontend_port = ports.take(), ports.take()
    fixture.mode(backend={"ttl_seconds": OWNED_TTL_SECONDS}, frontend={"ttl_seconds": OWNED_TTL_SECONDS})
    config = fixture.owned_launcher(backend_port, frontend_port, manifest_sha256="0" * 64)
    name = f"{TASK_PREFIX}InvalidIdentityBackend"
    register_task(name, fixture.python, fixture.launcher_arguments(config, "backend"), fixture.release)
    try:
        start_task(name)
        finished, waited, _ = wait_until(lambda: task_state(name)["state"] != "Running", 60, 0.5)
        state = task_state(name)
        direct = subprocess.run([str(fixture.python), "-B", str(fixture.application / "Run-AgroSatApplication.py"),
                                 "--configuration", str(config), "--component", "backend"],
                                capture_output=True, text=True, timeout=60)
    finally:
        unregister_task(name)
    report = json.loads(direct.stdout.strip().splitlines()[-1]) if direct.stdout.strip() else None
    return {
        "scenario": "invalid-identity", "port": backend_port, "task_finished": finished,
        "task_result": state["last_result"], "listener_after": listeners([backend_port])[backend_port],
        "lifecycle_events": fixture.lifecycle("backend"), "direct_exit_code": direct.returncode,
        "direct_report": report,
        "pass": (finished and state["last_result"] == 70 and listeners([backend_port])[backend_port] is None
                 and not fixture.lifecycle("backend") and direct.returncode == 70
                 and report.get("error_code") == "APPLICATION_MANIFEST_HASH_REJECTED"),
    }


def task_events(name: str, since: str) -> list[dict]:
    rows = ps_json(
        "$since = [datetime]::Parse(" + q(since) + ").ToLocalTime(); "
        "@(Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-TaskScheduler/Operational'; StartTime=$since} "
        f"-ErrorAction SilentlyContinue | Where-Object {{ $_.Message -like ('*' + {q(name)} + '*') }} | "
        "Sort-Object TimeCreated | ForEach-Object { [pscustomobject]@{ id=$_.Id; "
        "time=$_.TimeCreated.ToUniversalTime().ToString('o') } }) | ConvertTo-Json -Compress") or []
    return [rows] if isinstance(rows, dict) else rows


def scenario_child_exit(run_root: Path, ports: Ports, observe_seconds: int) -> dict:
    fixture = Fixture(run_root, "child-exit-recovery")
    backend_port, frontend_port = ports.take(), ports.take()
    fixture.mode(backend={"ttl_seconds": OWNED_TTL_SECONDS, "crash_after_seconds": 8},
                 frontend={"ttl_seconds": OWNED_TTL_SECONDS})
    config = fixture.owned_launcher(backend_port, frontend_port)
    name = f"{TASK_PREFIX}ChildExitBackend"
    restart_count = 2
    register_task(name, fixture.python, fixture.launcher_arguments(config, "backend"), fixture.release,
                  restart_count=restart_count, restart_minutes=1)
    since = utc()
    try:
        start_task(name)
        time.sleep(observe_seconds)
        state = task_state(name)
        events = task_events(name, since)
    finally:
        unregister_task(name)
    lifecycle = fixture.lifecycle("backend")
    starts = [item for item in lifecycle if item.get("status") == "started"]
    exits = [item for item in lifecycle if item.get("status") == "exited"]
    leftovers = [item for item in exits if [pid for pid in item["ownership"]["member_pids"]
                                           if pid != item["supervisor_pid"]]]
    return {
        "scenario": "child-exit-recovery", "restart_count_configured": restart_count,
        "restart_interval_minutes": 1, "observed_seconds": observe_seconds,
        "supervisor_starts": len(starts), "child_exit_codes": [item["exit_code"] for item in exits],
        "task_state_at_end": state, "task_scheduler_events": events,
        "members_left_at_exit": leftovers,
        "bounded": len(starts) <= 1 + restart_count,
        "pass": 1 <= len(starts) <= 1 + restart_count and all(item["exit_code"] == 3 for item in exits),
    }


SCENARIOS = {
    "app-backend-owned": lambda r, p, a: scenario_app(r, p, component="backend", descendant=False, legacy=False),
    "app-backend-descendant-owned": lambda r, p, a: scenario_app(r, p, component="backend", descendant=True, legacy=False),
    "app-frontend-descendant-owned": lambda r, p, a: scenario_app(r, p, component="frontend", descendant=True, legacy=False),
    "app-backend-descendant-legacy": lambda r, p, a: scenario_app(r, p, component="backend", descendant=True, legacy=True),
    "app-frontend-descendant-legacy": lambda r, p, a: scenario_app(r, p, component="frontend", descendant=True, legacy=True),
    "worker-collector-current": lambda r, p, a: scenario_worker(r, p, kind="collector"),
    "worker-notifications-current": lambda r, p, a: scenario_worker(r, p, kind="notifications"),
    "worker-collector-current-detached-provider": lambda r, p, a: scenario_worker(
        r, p, kind="collector", detach_provider=True),
    "second-launcher": lambda r, p, a: scenario_second_launcher(r, p),
    "invalid-identity": lambda r, p, a: scenario_invalid_identity(r, p),
    "child-exit-recovery": lambda r, p, a: scenario_child_exit(r, p, a.recovery_observe_seconds),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--scenario", action="append", choices=sorted(SCENARIOS))
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--recovery-observe-seconds", type=int, default=210)
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("TASK230_WINDOWS_REQUIRED")
    run_root = REHEARSAL_BASE / args.run_id
    run_root.mkdir(parents=True, exist_ok=False)
    args.evidence.mkdir(parents=True, exist_ok=True)
    results = []
    for scenario in args.scenario or list(SCENARIOS):
        started = utc()
        try:
            result = SCENARIOS[scenario](run_root, Ports(), args)
        except Exception as exc:  # evidence first: record, continue with the next scenario
            result = {"scenario": scenario, "pass": False, "error": f"{type(exc).__name__}: {exc}"[:1500]}
        result.update({"started_at": started, "finished_at": utc(), "run_root": str(run_root / scenario)})
        (args.evidence / f"{scenario}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        results.append({"scenario": scenario, "pass": result["pass"],
                        "interpretation": result.get("interpretation"), "error": result.get("error")})
        print(json.dumps(results[-1]), flush=True)
    summary = {"run_id": args.run_id, "repository_head": subprocess.run(
        ["git", "-C", str(REPOSITORY), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "results": results, "pass": all(item["pass"] for item in results)}
    (args.evidence / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
