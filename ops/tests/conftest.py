"""Shared fixtures for the TASK_230 control-plane tests."""
from __future__ import annotations

from pathlib import Path
import sys

RELEASE_TOOLING = Path(__file__).resolve().parents[1] / "release"
if str(RELEASE_TOOLING) not in sys.path:
    sys.path.insert(0, str(RELEASE_TOOLING))
