"""Static product and safety contract for TASK_209 Phase 9D."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "TASK_209_YIELD_MAP_IMPORT_CONTRACT.md"


def contract():
    return DOC.read_text(encoding="utf-8").lower()


def test_contract_defines_one_supported_provider_neutral_schema():
    text = contract()
    assert "yield_point_csv_v1" in text
    assert "`longitude`" in text
    assert "`latitude`" in text
    assert "`yield_value`" in text
    assert "`observed_at`" in text
    assert "`t_ha`" in text
    assert "`kg_ha`" in text
    assert "bushels per acre are unsupported" in text


def test_contract_requires_preview_rejections_and_field_intersection():
    text = contract()
    assert "preview" in text
    assert "5,000" in text
    assert "median absolute deviation" in text
    assert "outside_field" in text
    assert "st_covers" in text
    assert "one bounded postgis" in text
    assert "zero rejected rows" in text


def test_contract_requires_idempotency_provenance_and_atomic_persistence():
    text = contract()
    assert "idempotency-key" in text
    assert "preview fingerprint" in text
    assert "one transaction" in text
    assert "source_sha256" in text
    assert "source provider" in text
    assert "safe basename" in text
    assert "gist index" in text


def test_contract_requires_tenant_scope_and_bounded_reads():
    text = contract()
    assert "viewer" in text
    assert "cross-tenant" in text
    assert "return `404`" in text
    assert "lists are bounded" in text
    assert "no lazy loading" in text


def test_contract_forbids_satellite_yield_inference_and_production_apply():
    text = contract()
    assert "does not infer" in text
    assert "satellite indices" in text
    assert "production upload and production database execution are outside" in text
    assert "b-001" in text
