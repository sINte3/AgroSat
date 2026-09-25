"""Scheduled Task definitions, contracts and the PowerShell bridge.

A registered task is read as its Task Scheduler XML (``Export-ScheduledTask``)
and parsed here, so every contract check is a pure function that is tested
offline against fixtures.

The controller never registers or unregisters a task and never touches a
trigger: a release or rollback only replaces a task's action
(``Set-ScheduledTask -Action``) and then proves that triggers, settings and
principal are byte-for-byte what they were. Registration belongs to the
reviewed installers in ops/windows-task and ops/database.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import re
import subprocess
from typing import Any
import xml.etree.ElementTree as ElementTree

from .common import ControlPlaneError, sha256_bytes

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
SYSTEM_SID = "S-1-5-18"
TASK_NAME_PATTERN = re.compile(r"^\\[A-Za-z0-9_]{1,120}$")

# Production schedule contracts (accepted production truth, TASK_227/TASK_230).
SENTINEL_DAILY_TIME = "06:00:00"
CONTRACTS = {
    "application": {"execution_time_limit": "PT0S", "restart_count": 3, "restart_interval": "PT1M"},
    "sentinel": {"execution_time_limit": "PT6H", "restart_count": 3, "restart_interval": "PT15M"},
    "notifications": {"execution_time_limit": "PT10M", "restart_count": 3, "restart_interval": "PT5M",
                      "repetition_interval": "PT15M"},
}


@dataclass(frozen=True)
class TaskAction:
    command: str
    arguments: str
    working_directory: str

    def evidence(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class TaskDefinition:
    name: str
    principal_user: str
    run_level: str
    multiple_instances: str
    execution_time_limit: str
    restart_count: int | None
    restart_interval: str | None
    start_when_available: bool
    enabled: bool
    triggers: tuple[dict[str, Any], ...]
    actions: tuple[TaskAction, ...]
    xml_sha256: str = ""
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    def schedule_identity(self) -> dict[str, Any]:
        """Everything a rebind must leave unchanged."""
        return {"principal_user": self.principal_user, "run_level": self.run_level,
                "multiple_instances": self.multiple_instances, "execution_time_limit": self.execution_time_limit,
                "restart_count": self.restart_count, "restart_interval": self.restart_interval,
                "start_when_available": self.start_when_available, "enabled": self.enabled,
                "triggers": list(self.triggers)}

    def evidence(self) -> dict[str, Any]:
        return {"name": self.name, **self.schedule_identity(),
                "actions": [action.evidence() for action in self.actions], "xml_sha256": self.xml_sha256}


def _text(node, path: str, default: str | None = None) -> str | None:
    found = node.find(path, NS)
    return default if found is None or found.text is None else found.text.strip()


def parse_task_xml(name: str, xml_text: str) -> TaskDefinition:
    body = re.sub(r"^\s*<\?xml[^>]*\?>", "", xml_text, count=1)
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        raise ControlPlaneError("TASK_DEFINITION_UNREADABLE", name) from None
    settings = root.find("t:Settings", NS)
    principal = root.find("t:Principals/t:Principal", NS)
    if settings is None or principal is None:
        raise ControlPlaneError("TASK_DEFINITION_UNREADABLE", name)
    restart = settings.find("t:RestartOnFailure", NS)
    triggers = []
    trigger_root = root.find("t:Triggers", NS)
    for trigger in list(trigger_root) if trigger_root is not None else []:
        kind = trigger.tag.split("}", 1)[-1]
        triggers.append({
            "kind": kind,
            "enabled": (_text(trigger, "t:Enabled", "true") or "true").lower() == "true",
            "start_boundary": _text(trigger, "t:StartBoundary"),
            "days_interval": _text(trigger, "t:ScheduleByDay/t:DaysInterval"),
            "repetition_interval": _text(trigger, "t:Repetition/t:Interval"),
            "repetition_duration": _text(trigger, "t:Repetition/t:Duration"),
        })
    actions = tuple(
        TaskAction(_text(item, "t:Command", "") or "", _text(item, "t:Arguments", "") or "",
                   _text(item, "t:WorkingDirectory", "") or "")
        for item in root.findall("t:Actions/t:Exec", NS)
    )
    return TaskDefinition(
        name=name,
        principal_user=_text(principal, "t:UserId", "") or "",
        run_level=_text(principal, "t:RunLevel", "LeastPrivilege") or "",
        multiple_instances=_text(settings, "t:MultipleInstancesPolicy", "IgnoreNew") or "",
        execution_time_limit=_text(settings, "t:ExecutionTimeLimit", "PT72H") or "",
        restart_count=int(_text(restart, "t:Count")) if restart is not None and _text(restart, "t:Count") else None,
        restart_interval=_text(restart, "t:Interval") if restart is not None else None,
        start_when_available=(_text(settings, "t:StartWhenAvailable", "false") or "").lower() == "true",
        enabled=(_text(settings, "t:Enabled", "true") or "true").lower() == "true",
        triggers=tuple(triggers),
        actions=actions,
        xml_sha256=sha256_bytes(xml_text.encode("utf-8")),
    )


def _local_time_of(boundary: str | None) -> str | None:
    if not boundary:
        return None
    try:
        return datetime.fromisoformat(boundary.replace("Z", "+00:00")).strftime("%H:%M:%S")
    except ValueError:
        return None


def backup_task_violations(definition: TaskDefinition, *, production: bool) -> list[str]:
    """The backup task's schedule comes from its policy; its shape is fixed."""
    problems = []
    if not re.fullmatch(r"S-1-[0-9-]+", definition.principal_user):
        problems.append(f"principal:{definition.principal_user}")
    if definition.multiple_instances != "IgnoreNew":
        problems.append(f"multiple_instances:{definition.multiple_instances}")
    if not definition.start_when_available:
        problems.append("start_when_available:false")
    if (definition.restart_count or 0) > 3:
        problems.append(f"restart_count:{definition.restart_count}")
    if definition.execution_time_limit in ("PT0S", "PT72H") or definition.execution_time_limit.endswith("D"):
        problems.append(f"execution_time_limit:{definition.execution_time_limit}")
    if len(definition.actions) != 1:
        problems.append(f"action_count:{len(definition.actions)}")
    triggers = definition.triggers
    if production and (len(triggers) != 1 or triggers[0]["kind"] != "CalendarTrigger" or triggers[0]["days_interval"] != "1"):
        problems.append(f"backup_triggers:{len(triggers)}")
    if not production and triggers:
        problems.append(f"rehearsal_trigger_count:{len(triggers)}")
    return problems


def contract_violations(definition: TaskDefinition, kind: str, *, production: bool) -> list[str]:
    """Violations of the application, sentinel, notifications or backup task contract."""
    if kind == "backup":
        return backup_task_violations(definition, production=production)
    contract = CONTRACTS["application" if kind in ("backend", "frontend") else kind]
    problems = []
    if definition.principal_user not in (SYSTEM_SID,):
        problems.append(f"principal:{definition.principal_user}")
    if definition.multiple_instances != "IgnoreNew":
        problems.append(f"multiple_instances:{definition.multiple_instances}")
    if not definition.start_when_available:
        problems.append("start_when_available:false")
    if definition.execution_time_limit != contract["execution_time_limit"]:
        problems.append(f"execution_time_limit:{definition.execution_time_limit}")
    if definition.restart_count != contract["restart_count"] or definition.restart_interval != contract["restart_interval"]:
        problems.append(f"restart:{definition.restart_count}/{definition.restart_interval}")
    if len(definition.actions) != 1:
        problems.append(f"action_count:{len(definition.actions)}")
    triggers = definition.triggers
    if not production:
        # Rehearsal tasks are started explicitly and must never fire by themselves.
        if triggers:
            problems.append(f"rehearsal_trigger_count:{len(triggers)}")
        return problems
    if kind in ("backend", "frontend"):
        if [trigger["kind"] for trigger in triggers] != ["BootTrigger"]:
            problems.append("triggers:" + ",".join(trigger["kind"] for trigger in triggers))
    elif kind == "sentinel":
        if len(triggers) != 1:
            problems.append(f"sentinel_trigger_count:{len(triggers)}")
        for trigger in triggers:
            if trigger["kind"] != "CalendarTrigger" or trigger["days_interval"] != "1":
                problems.append(f"sentinel_trigger_kind:{trigger['kind']}/{trigger['days_interval']}")
            if _local_time_of(trigger["start_boundary"]) != SENTINEL_DAILY_TIME:
                problems.append(f"sentinel_trigger_time:{_local_time_of(trigger['start_boundary'])}")
            if not trigger["enabled"]:
                problems.append("sentinel_trigger_disabled")
    elif kind == "notifications":
        if len(triggers) != 1:
            problems.append(f"notifications_trigger_count:{len(triggers)}")
        for trigger in triggers:
            if trigger["repetition_interval"] != contract["repetition_interval"]:
                problems.append(f"notifications_interval:{trigger['repetition_interval']}")
            if not trigger["enabled"]:
                problems.append("notifications_trigger_disabled")
    return problems


def rebind_violations(before: TaskDefinition, after: TaskDefinition, expected_action: TaskAction) -> list[str]:
    """A rebind may change exactly one thing: the action."""
    problems = []
    if before.schedule_identity() != after.schedule_identity():
        problems.append("schedule_identity_changed")
    if after.actions != (expected_action,):
        problems.append("action_not_bound_as_expected")
    return problems


# ---------------------------------------------------------------- PowerShell bridge

def _quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def split_name(name: str) -> tuple[str, str]:
    if not TASK_NAME_PATTERN.fullmatch(name):
        raise ControlPlaneError("TASK_NAME_REJECTED", name)
    return "\\", name[1:]


def powershell(script: str, *, timeout: int = 180) -> str:
    """Run a PowerShell snippet; any error is terminating; output is returned."""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
         "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; "
         "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + script + "\nexit 0"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode != 0:
        raise ControlPlaneError("POWERSHELL_OPERATION_FAILED", " ".join((result.stderr or result.stdout).split())[:400])
    return result.stdout


class WindowsTasks:
    """Scheduled Task operations for exactly-named tasks (no wildcards)."""

    def export(self, name: str) -> TaskDefinition | None:
        path, leaf = split_name(name)
        output = powershell(
            f"$t = Get-ScheduledTask -TaskPath {_quote(path)} -TaskName {_quote(leaf)} -ErrorAction SilentlyContinue; "
            f"if ($t) {{ Export-ScheduledTask -TaskPath {_quote(path)} -TaskName {_quote(leaf)} }}")
        text = output.strip()
        return parse_task_xml(name, text) if text else None

    def state(self, name: str) -> str | None:
        path, leaf = split_name(name)
        output = powershell(
            f"$t = Get-ScheduledTask -TaskPath {_quote(path)} -TaskName {_quote(leaf)} -ErrorAction SilentlyContinue; "
            "if ($t) { [string]$t.State }").strip()
        return output or None

    def stop(self, name: str) -> None:
        path, leaf = split_name(name)
        powershell(f"Stop-ScheduledTask -TaskPath {_quote(path)} -TaskName {_quote(leaf)}")

    def start(self, name: str) -> None:
        path, leaf = split_name(name)
        powershell(f"Start-ScheduledTask -TaskPath {_quote(path)} -TaskName {_quote(leaf)}")

    def set_enabled(self, name: str, enabled: bool) -> None:
        path, leaf = split_name(name)
        verb = "Enable-ScheduledTask" if enabled else "Disable-ScheduledTask"
        powershell(f"{verb} -TaskPath {_quote(path)} -TaskName {_quote(leaf)} | Out-Null")

    def set_action(self, name: str, action: TaskAction) -> None:
        path, leaf = split_name(name)
        powershell(
            f"$a = New-ScheduledTaskAction -Execute {_quote(action.command)} -Argument {_quote(action.arguments)} "
            f"-WorkingDirectory {_quote(action.working_directory)}; "
            f"Set-ScheduledTask -TaskPath {_quote(path)} -TaskName {_quote(leaf)} -Action $a | Out-Null")

    def engine_pids(self, name: str) -> list[int]:
        path, leaf = split_name(name)
        output = powershell(
            "$s = New-Object -ComObject Schedule.Service; $s.Connect(); "
            f"$t = $s.GetFolder('\\').GetTask({_quote(leaf)}); "
            "@($t.GetInstances(0) | ForEach-Object { [int]$_.EnginePID }) -join ','")
        return [int(item) for item in output.strip().split(",") if item.strip()]

    def last_result(self, name: str) -> int | None:
        path, leaf = split_name(name)
        output = powershell(
            f"$i = Get-ScheduledTaskInfo -TaskPath {_quote(path)} -TaskName {_quote(leaf)} -ErrorAction SilentlyContinue; "
            "if ($i) { [int64]$i.LastTaskResult }").strip()
        return int(output) if output else None
