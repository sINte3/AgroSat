from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs" / "TASK_209_WIALON_READ_ONLY_CONTRACT.md"


def contract_text() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def test_contract_is_read_only_and_server_side():
    value = contract_text()
    assert "does not create commands" in value
    assert "browser never receives a Wialon token" in value
    assert "server-side per tenant" in value
    assert "never logged" in value


def test_contract_is_tenant_scoped_and_fail_closed():
    value = contract_text()
    assert "same enterprise" in value
    assert "Cache keys include the enterprise and field identifiers" in value
    assert "explicit `unsupported` result" in value
    assert "never fixture or mock data in a production path" in value


def test_contract_bounds_provider_reads():
    value = contract_text()
    for required in (
        "bounded timeouts",
        "bounded pagination",
        "per-tenant request limiter",
        "allowlisted set of sensor values",
        "stale flag",
    ):
        assert required in value


def test_contract_defines_safe_ui_and_live_blocker():
    value = contract_text()
    assert "contains no write controls" in value
    assert "cancels requests and rejects stale responses" in value
    assert "deferred to the next pilot wave: Integrated Operations" in value
    assert "disabled by default" in value
    assert "does not require a Wialon token or mapping" in value
