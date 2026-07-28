"""Operational satellite state is explicit, tenant-scoped, and bounded."""

import ast
from pathlib import Path

from services import satellite_data_quality


BACKEND = Path(__file__).resolve().parents[1]


def test_quality_summary_contract_includes_last_observation_and_failed_counts():
    source = Path(satellite_data_quality.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    required = {
        "latest_captured_date",
        "fresh_count",
        "stale_count",
        "missing_count",
        "suspicious_value_count",
        "stale_field_index_pairs",
        "missing_field_index_pairs",
    }
    assert required <= literals


def test_quality_router_is_authenticated_and_tenant_scoped():
    source = (BACKEND / "api" / "satellite_data_quality.py").read_text(
        encoding="utf-8"
    )
    assert "Depends(get_current_active_user)" in source
    assert "Depends(require_enterprise_scope)" in source
    assert "effective_enterprise_id = enterprise_scope" in source


def test_quality_route_has_explicit_problem_field_limit():
    source = (BACKEND / "api" / "satellite_data_quality.py").read_text(
        encoding="utf-8"
    )
    assert "le=1000" in source
    assert "limit=limit" in source
