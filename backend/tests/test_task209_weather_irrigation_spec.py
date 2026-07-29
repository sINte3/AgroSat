from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs" / "TASK_209_WEATHER_IRRIGATION_WORKFLOW.md"


def contract_text() -> str:
    return CONTRACT.read_text(encoding="utf-8").lower()


def test_contract_integrates_existing_operational_loop_without_diagnosis():
    value = contract_text()
    assert "existing inspection, corrective-action, closure" in value
    assert "cannot confirm irrigation failure" in value
    assert "does not establish causality" in value
    assert "confirmed irrigation or weather cause requires an inspection result" in value


def test_contract_defines_structured_inspection_source_and_events():
    value = contract_text()
    assert "`irrigation_context`" in value
    assert "`water_stress_suspicion`" in value
    assert "`weather_water_deficit`" in value
    assert "`irrigation_events`" in value
    assert "`irrigation_applied`" in value
    assert "`irrigation_interrupted`" in value
    assert "`human_reported`" in value


def test_contract_defines_bounded_tenant_authorized_api():
    value = contract_text()
    assert "get /api/irrigation-context/fields/{field_id}" in value
    assert "post /api/irrigation-context/fields/{field_id}/events" in value
    assert "at most 100" in value
    assert "denies viewers" in value
    assert "idempotency-key" in value
    assert "cross-tenant" in value
    assert "tenant-safe" in value
    assert "no lazy loading" in value


def test_contract_defines_provider_provenance_and_fail_closed_behavior():
    value = contract_text()
    assert "asia/tashkent" in value
    assert "explicit timeout" in value
    assert "`available` or `unavailable`" in value
    assert "no production mock fallback" in value
    assert "provider and retrieval provenance" in value


def test_contract_defines_migration_and_acceptance_boundaries():
    value = contract_text()
    assert "foreign keys, checks, unique idempotency" in value
    assert "safe nullable optional inspection link" in value
    assert "no irrigation-context inspections remain" in value
    assert "no migration is applied to production" in value
    assert "upgrade/downgrade source tests" in value
