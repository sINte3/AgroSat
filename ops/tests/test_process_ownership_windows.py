"""Real Windows Job Object behavior of the ownership helper (Windows only).

These run on the Windows CI job and locally. They prove the kernel contract the
application supervisor relies on; the Scheduled Task qualification
(ops/qualification/Run-Task230ProcessOwnershipQualification.py) proves it
end to end under Task Scheduler.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Objects")

RELEASE = Path(__file__).resolve().parents[1] / "release"


def alive(pid: int) -> bool:
    from controlplane import winproc
    return pid in winproc.snapshot()


def wait_gone(pids: list[int], seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not any(alive(pid) for pid in pids):
            return True
        time.sleep(0.1)
    return False


OWNER = r'''
import subprocess, sys, time
sys.path.insert(0, r"{release}")
import agrosat_process_ownership as ownership
owner = ownership.ProcessTreeOwnership.adopt_current_process()
evidence = owner.evidence()
assert evidence["kill_on_job_close"] and not evidence["breakaway_allowed"] and not evidence["silent_breakaway_allowed"]
child = subprocess.Popen([sys.executable, "-c",
    "import subprocess, sys, time; g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)']);"
    " print(g.pid, flush=True); time.sleep(300)"], stdout=subprocess.PIPE, text=True,
    creationflags=subprocess.CREATE_NO_WINDOW)
grandchild = int(child.stdout.readline())
print(child.pid, grandchild, flush=True)
members = owner.member_process_ids()
assert child.pid in members, members
{ending}
'''


@pytest.mark.parametrize("ending", ["time.sleep(0.5)", "import os; os._exit(9)"])
def test_descendants_end_when_the_owner_ends(tmp_path, ending):
    script = tmp_path / "owner.py"
    script.write_text(OWNER.format(release=RELEASE, ending=ending), encoding="utf-8")
    owner = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60)
    assert owner.stdout.strip(), owner.stderr
    child, grandchild = (int(value) for value in owner.stdout.split())
    assert wait_gone([child, grandchild]), "owned descendants outlived their owner"


def test_owner_killed_from_outside_still_takes_its_tree(tmp_path):
    script = tmp_path / "owner.py"
    script.write_text(OWNER.format(release=RELEASE, ending="time.sleep(300)"), encoding="utf-8")
    owner = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    child, grandchild = (int(value) for value in owner.stdout.readline().split())
    owner.kill()  # TerminateProcess, as Task Scheduler does
    owner.wait(timeout=30)
    assert wait_gone([child, grandchild])


def test_breakaway_is_refused_inside_an_owned_tree(tmp_path):
    script = tmp_path / "breakaway.py"
    script.write_text(rf'''
import subprocess, sys
sys.path.insert(0, r"{RELEASE}")
import agrosat_process_ownership as ownership
ownership.ProcessTreeOwnership.adopt_current_process()
try:
    subprocess.Popen([sys.executable, "-c", "pass"], creationflags=0x01000000)  # CREATE_BREAKAWAY_FROM_JOB
except OSError:
    print("REFUSED")
else:
    print("ESCAPED")
''', encoding="utf-8")
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60)
    assert result.stdout.strip() == "REFUSED", result.stderr
