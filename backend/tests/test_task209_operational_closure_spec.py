"""Guard the agreed TASK_209 operational closure contract."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs" / "TASK_209_OPERATIONAL_CLOSURE_SPEC.md"


def normalized() -> str:
    return " ".join(SPEC.read_text(encoding="utf-8").split())


def test_spec_is_current_and_not_production_applied():
    source = SPEC.read_text(encoding="utf-8")
    assert "Status: implementation contract, not production-applied." in source
    assert "B-001" in source
    assert "does not backfill a confirmed cause" in source


def test_spec_has_required_domain_concepts():
    source = SPEC.read_text(encoding="utf-8").lower()
    for concept in (
        "confirmed-cause codes",
        "evidence metadata",
        "corrective action",
        "action owner",
        "closure reason",
        "reopen",
        "verification confidence",
        "insufficient_data",
        "operational_audit_events",
    ):
        assert concept in source


def test_spec_has_role_tenant_idempotency_and_conflict_rules():
    source = normalized()
    assert "Cross-tenant identifiers return non-enumerable 404" in source
    assert "`viewer` is read-only" in source
    assert "Every new write requires `Idempotency-Key`" in source
    assert "optimistic" in source
    assert "There is no lazy loading." in source


def test_verification_contract_is_deterministic_and_noncausal():
    source = normalized()
    assert "observation_direction_v1" in source
    assert "at least three calendar days" in source
    assert "at least 50 percent valid pixels" in source
    assert "at most 30 percent cloud" in source
    assert "rounded to four decimal places" in source
    assert "not proof that the action caused the change" in source


def test_api_collections_are_bounded():
    source = SPEC.read_text(encoding="utf-8")
    assert "limit <= 200" in source
    assert "offset <= 10000" in source
