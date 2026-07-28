"""Database configuration must contain no embedded operational credential."""

import ast
from pathlib import Path

import database


BACKEND = Path(__file__).resolve().parents[1]


def _class_assignment_value(path: Path, class_name: str, field_name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    settings_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    assignment = next(
        node
        for node in settings_class.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == field_name
    )
    return ast.literal_eval(assignment.value)


def test_database_url_default_is_empty():
    assert _class_assignment_value(
        BACKEND / "config.py",
        "Settings",
        "database_url",
    ) == ""


def test_unconfigured_database_target_is_explicitly_unreachable():
    target = database.UNCONFIGURED_DATABASE_TARGET
    assert target.host == "configuration-required.invalid"
    assert target.database == "configuration_required"
    assert target.username is None
    assert target.password is None


def test_live_configuration_files_contain_no_literal_database_password():
    candidates = (
        BACKEND.parent / ".env.example",
        BACKEND.parent / "docker-compose.yml",
        BACKEND / "alembic.ini",
        BACKEND / "config.py",
    )
    for candidate in candidates:
        text = candidate.read_text(encoding="utf-8")
        assert "agrosat_secret" not in text
