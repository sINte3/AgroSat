"""Static product and safety contract for TASK_209 Phase 9E."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "TASK_209_PRODUCTIVITY_ZONE_CONTRACT.md"


def contract():
    return DOC.read_text(encoding="utf-8").lower()


def test_contract_requires_sufficient_measured_history():
    text = contract()
    assert "yield_grid_stability_v1" in text
    assert "at least three distinct" in text
    assert "at least 20 valid measured points" in text
    assert "at least 60 measured points" in text
    assert "at least two seasons" in text
    assert "insufficient_data" in text


def test_contract_defines_deterministic_normalization_and_smoothing():
    text = contract()
    assert "median absolute deviation" in text
    assert "1.4826 * mad" in text
    assert "clamp" in text
    assert "30 metre" in text
    assert "weight 2" in text
    assert "eight immediate neighbours" in text
    assert "tercile" in text


def test_contract_requires_geometry_and_area_reconciliation():
    text = contract()
    assert "epsg:4326" in text
    assert "clipped to the field" in text
    assert "do not overlap" in text
    assert "0.01 ha" in text
    assert "area_delta_ha" in text
    assert "unzoned area" in text


def test_contract_requires_provenance_reproducibility_and_cli_isolation():
    text = contract()
    assert "source sha-256" in text
    assert "64-character run key" in text
    assert "same inputs produce the same run key" in text
    assert "standalone bounded cli" in text
    assert "never a fastapi scheduler" in text
    assert "checkpoint/resume" in text
    assert "no mock or synthetic result is written" in text


def test_contract_forbids_prescriptions_and_records_external_blocker():
    text = contract()
    assert "not agronomic prescriptions" in text
    assert "not derived from satellite indices alone" in text
    assert "b-001" in text
