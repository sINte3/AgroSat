"""Durable release/rollback state: one directory per release id (TASK_230 Parts F, G, S).

``<control_root>/releases/<release_id>/``

* ``state.json``   the machine-readable record, rewritten atomically at every
                   transition; it alone decides where a re-run resumes
* ``events.jsonl`` append-only transition log
* ``gates/NN_<GATE>-attemptK.json``  immutable evidence of every gate attempt
* ``snapshots/``   task definitions and configurations captured before change
* ``summary.md``   human summary, written from the JSON (second, never first)

The identity a release id is bound to is hashed at creation. A re-run with any
other identity is refused; a terminal release id is never executed again;
completed ids are also appended to ``<control_root>/completed-releases.jsonl``,
which the authorization check reads to refuse replay.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
from typing import Any, Iterator

from .common import (
    ControlPlaneError, append_jsonl, canonical_json, iso, read_json, sha256_bytes, utc_now, write_json_atomic,
    write_json_immutable,
)

RELEASE_GATES = ("PRECHECK", "BACKUP", "MATERIALIZE", "VALIDATE", "MIGRATION_PLAN", "MIGRATE",
                 "SWITCH_BACKEND", "VERIFY_BACKEND", "SWITCH_FRONTEND", "VERIFY_FRONTEND",
                 "REBIND_WORKERS", "VERIFY_WORKERS", "FINAL_HEALTH", "COMMIT")
ROLLBACK_GATES = ("PRECHECK", "ROLLBACK_PLAN", "BACKUP", "DATABASE", "SWITCH_BACKEND", "VERIFY_BACKEND",
                  "SWITCH_FRONTEND", "VERIFY_FRONTEND", "REBIND_WORKERS", "VERIFY_WORKERS", "FINAL_HEALTH", "COMMIT")
TERMINAL = ("completed", "failed", "rolled_back", "manual_recovery_required")


def identity_sha256(identity: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(identity))


def used_release_ids(control_root: Path) -> set[str]:
    """Release ids already executed to a terminal state (replay protection)."""
    ids = set()
    registry = Path(control_root) / "completed-releases.jsonl"
    if registry.exists():
        for line in registry.read_text(encoding="utf-8").splitlines():
            if line.strip():
                ids.add(json.loads(line)["release_id"])
    releases = Path(control_root) / "releases"
    if releases.is_dir():
        for child in releases.iterdir():
            state = child / "state.json"
            if state.exists():
                try:
                    if read_json(state, "RELEASE_STATE_UNREADABLE").get("status") in TERMINAL:
                        ids.add(child.name)
                except ControlPlaneError:
                    ids.add(child.name)
    return ids


class ReleaseState:
    def __init__(self, directory: Path, data: dict[str, Any]):
        self.directory = Path(directory)
        self.data = data

    # ---------------------------------------------------------------- lifecycle
    @classmethod
    def open(cls, control_root: Path, release_id: str, identity: dict[str, Any], gates: tuple[str, ...],
             *, operation: str) -> "ReleaseState":
        directory = Path(control_root) / "releases" / release_id
        path = directory / "state.json"
        digest = identity_sha256(identity)
        if path.exists():
            data = read_json(path, "RELEASE_STATE_UNREADABLE")
            if data.get("identity_sha256") != digest:
                raise ControlPlaneError("RELEASE_IDENTITY_CONTRADICTION",
                                        "this release id is bound to a different identity")
            status = data.get("status")
            if status in TERMINAL:
                raise ControlPlaneError(f"RELEASE_ALREADY_{status.upper()}",
                                        "a terminal release id never executes again; use a new release id")
            state = cls(directory, data)
            state.event("resumed", gate=data.get("current_gate"))
            return state
        directory.mkdir(parents=True, exist_ok=False)
        now = iso(utc_now())
        data = {
            "schema_version": 1, "kind": "agrosat_release_state", "release_id": release_id,
            "operation": operation, "identity": identity, "identity_sha256": digest, "gates": list(gates),
            "status": "in_progress", "current_gate": gates[0], "gate_results": {},
            "previous_sha": identity["expected_current_sha"], "candidate_sha": identity["candidate_sha"],
            "db_revision_before": None, "db_revision_after": None, "migration": {"started": False, "applied_steps": []},
            "backup": None, "started_at": now, "completed_at": None, "updated_at": now,
            "rollback_required": False, "rollback_result": None, "facts": {},
        }
        state = cls(directory, data)
        state.save()
        state.event("created", identity_sha256=digest)
        return state

    def save(self) -> None:
        self.data["updated_at"] = iso(utc_now())
        write_json_atomic(self.directory / "state.json", self.data)

    def event(self, kind: str, **fields: Any) -> None:
        append_jsonl(self.directory / "events.jsonl", {"time": iso(utc_now()), "event": kind, **fields})

    # ---------------------------------------------------------------- gates
    @property
    def gates(self) -> tuple[str, ...]:
        return tuple(self.data["gates"])

    def pending_gates(self) -> list[str]:
        gates = self.gates
        return list(gates[gates.index(self.data["current_gate"]):]) if self.data["current_gate"] in gates else []

    def _attempt_path(self, gate: str) -> Path:
        result = self.data["gate_results"].setdefault(gate, {"status": "pending", "attempts": 0, "evidence": []})
        result["attempts"] += 1
        index = self.gates.index(gate) + 1 if gate in self.gates else 99
        return self.directory / "gates" / f"{index:02d}_{gate}-attempt{result['attempts']}.json"

    def begin(self, gate: str) -> None:
        self.data["current_gate"] = gate
        result = self.data["gate_results"].setdefault(gate, {"status": "pending", "attempts": 0, "evidence": []})
        interrupted = result["status"] == "running"
        if interrupted:
            # The previous run ended inside this gate; the gate runs again and must
            # establish from the live system what was already done.
            result["interruptions"] = result.get("interruptions", 0) + 1
        result["starts"] = result.get("starts", 0) + 1
        result["status"] = "running"
        result["started_at"] = iso(utc_now())
        self.save()
        self.event("gate_started", gate=gate, resumed_after_interruption=interrupted)

    def record(self, gate: str, status: str, evidence: dict[str, Any]) -> None:
        path = self._attempt_path(gate)
        record = {"gate": gate, "status": status, "release_id": self.data["release_id"],
                  "recorded_at": iso(utc_now()), **evidence}
        write_json_immutable(path, record)
        result = self.data["gate_results"][gate]
        result.update(status=status, finished_at=iso(utc_now()))
        result["evidence"].append(str(path.relative_to(self.directory)).replace("\\", "/"))
        summary = evidence.get("summary")
        if summary is not None:
            result["summary"] = summary
        self.save()
        self.event("gate_" + status, gate=gate, evidence=result["evidence"][-1])

    def advance(self, gate: str) -> None:
        gates = self.gates
        index = gates.index(gate)
        self.data["current_gate"] = gates[index + 1] if index + 1 < len(gates) else "DONE"
        self.save()

    def finish(self, status: str) -> None:
        if status not in TERMINAL:
            raise ValueError(status)
        self.data["status"] = status
        self.data["completed_at"] = iso(utc_now())
        self.save()
        self.event("finished", status=status)

    # ---------------------------------------------------------------- snapshots
    def snapshot(self, name: str, value: Any) -> str:
        path = self.directory / "snapshots" / name
        return write_json_immutable(path, value)

    def read_snapshot(self, name: str) -> Any:
        return read_json(self.directory / "snapshots" / name, "RELEASE_SNAPSHOT_MISSING")


def register_terminal(control_root: Path, state: ReleaseState) -> None:
    append_jsonl(Path(control_root) / "completed-releases.jsonl", {
        "release_id": state.data["release_id"], "operation": state.data["operation"], "status": state.data["status"],
        "candidate_sha": state.data["candidate_sha"], "previous_sha": state.data["previous_sha"],
        "completed_at": state.data["completed_at"]})


@contextmanager
def controller_lock(control_root: Path) -> Iterator[None]:
    """One controller at a time per control root (a second run fails immediately)."""
    Path(control_root).mkdir(parents=True, exist_ok=True)
    path = Path(control_root) / "controller.lock"
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except ImportError:  # non-Windows test hosts
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ControlPlaneError("CONTROLLER_BUSY", "another release or rollback holds the control root") from None
        except OSError:
            raise ControlPlaneError("CONTROLLER_BUSY", "another release or rollback holds the control root") from None
        try:
            yield
        finally:
            stream.seek(0)
            try:
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            except ImportError:
                pass


def write_summary(state: ReleaseState) -> None:
    data = state.data
    lines = [f"# {data['operation'].title()} {data['release_id']}", "",
             f"- status: **{data['status']}**", f"- previous: `{data['previous_sha']}`",
             f"- candidate: `{data['candidate_sha']}`",
             f"- database revision: `{data['db_revision_before']}` -> `{data['db_revision_after']}`",
             f"- started: {data['started_at']}  completed: {data['completed_at']}",
             f"- rollback required: {data['rollback_required']}", "", "| gate | status | attempts |", "|---|---|---|"]
    for gate in data["gates"]:
        result = data["gate_results"].get(gate, {})
        lines.append(f"| {gate} | {result.get('status', 'not reached')} | {result.get('attempts', 0)} |")
    if data.get("rollback_result"):
        lines += ["", "## Rollback", "", "```json", json.dumps(data["rollback_result"], indent=2)[:6000], "```"]
    lines += ["", "Machine-readable evidence: `state.json`, `events.jsonl`, `gates/`."]
    (state.directory / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
