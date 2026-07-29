from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs" / "TASK_209_COMMERCIAL_TENANT_BOUNDARY.md"


def _contract() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def test_contract_keeps_enterprise_as_rollout_tenant_boundary():
    text = _contract()
    assert "existing `enterprises` row remains the operational tenant boundary" in text
    assert "legacy `users.enterprise_id` remains the" in text
    assert "compatibility scope" in text


def test_contract_requires_tenant_foreign_keys_indexes_and_negative_tests():
    text = _contract()
    assert "explicit `enterprise_id` foreign key" in text
    assert "leading column is `enterprise_id`" in text
    assert "Cross-tenant IDs return a non-disclosing not-found response" in text


def test_contract_namespaces_are_shared_and_non_sensitive():
    text = _contract()
    for value in ("tenants/42/", "agrosat:tenant:42:", "tenant.42.", "tenant-42/"):
        assert value in text
    assert "emails, or provider secrets are never" in text


def test_contract_never_stores_or_returns_provider_secret_material():
    text = _contract()
    assert "never a token, password, connection URL, or Authorization value" in text
    assert "never" in text[text.index("The API returns only") :]
    assert "returns the reference itself" in text


def test_contract_makes_export_deletion_reviewed_non_destructive_workflows():
    text = _contract()
    assert "API requests do not delete tenant data" in text
    assert "independently authorized executor" in text
    assert "Only an admin may approve or reject deletion" in text


def test_contract_excludes_payment_processing_and_web_scheduler():
    text = _contract()
    assert "No card, payment," in text
    assert "invoice-charge, refund" in text
    assert "never runs in FastAPI lifespan" in text
