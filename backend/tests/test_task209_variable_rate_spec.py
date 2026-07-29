"""Static product and safety contract for TASK_209 Phase 9F."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "TASK_209_VARIABLE_RATE_SAFETY_CONTRACT.md"


def contract():
    return DOC.read_text(encoding="utf-8").lower()


def test_contract_forbids_autonomous_satellite_prescriptions():
    text = contract()
    assert "does not create autonomous agronomic prescriptions" in text
    assert "cannot determine fertilizer, seed, pesticide, or irrigation rates" in text
    assert "every numeric rate is entered by an authorized human" in text


def test_contract_requires_bounds_zones_equipment_and_context():
    text = contract()
    for phrase in (
        "crop and season context",
        "explicit minimum and maximum rates",
        "explicit low, medium, and high zone rates",
        "equipment capability",
        "safety acknowledgement",
        "yield_grid_stability_v1",
    ):
        assert phrase in text


def test_contract_defines_role_approval_version_and_conflicts():
    text = contract()
    assert "draft -> approved" in text
    assert "admin and manager" in text
    assert "agronomists may create drafts" in text
    assert "viewers are read-only" in text
    assert "return `409`" in text
    assert "immutable audit events" in text


def test_contract_defines_tenant_safe_deterministic_geojson_export():
    text = contract()
    assert "cross-tenant object identifiers return `404`" in text
    assert "one feature per available productivity class" in text
    assert "epsg:4326" in text
    assert "draft exports are visibly marked `draft`" in text
    assert "does not convert units" in text


def test_contract_defines_bounded_api_frontend_and_external_blocker():
    text = contract()
    assert text.count("/api/variable-rate-recommendations") >= 6
    assert "all lists are bounded" in text
    assert "44-pixel touch targets" in text
    assert "cancels stale requests" in text
    assert "b-001" in text
