from pathlib import Path
from zoneinfo import ZoneInfo


BACKEND = Path(__file__).resolve().parents[1]


def test_tashkent_timezone_is_available():
    assert ZoneInfo("Asia/Tashkent").key == "Asia/Tashkent"


def test_tzdata_runtime_dependency_is_pinned():
    requirements = (BACKEND / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert "tzdata==2026.3" in requirements
