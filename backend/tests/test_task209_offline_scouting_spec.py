from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs" / "TASK_209_OFFLINE_SCOUTING_CONTRACT.md"


def text() -> str:
    return SPEC.read_text(encoding="utf-8")


def test_offline_contract_is_partitioned_and_purged_on_logout():
    value = text()
    assert "enterprise_id:user_id" in value
    assert "Logout deletes the complete offline scouting database" in value
    assert "access tokens, passwords, cookies, or authorization headers" in value


def test_offline_contract_is_bounded_and_metadata_only():
    value = text()
    for required in (
        "at most 100 inspection snapshots",
        "at most 100 drafts",
        "at most 200 queue items",
        "at most 20 evidence metadata records",
        "no binary attachment payload",
    ):
        assert required in value


def test_offline_sync_contract_is_ordered_idempotent_and_conflict_safe():
    value = text()
    assert "inspection result\n→ evidence metadata in draft order\n→ corrective action" in value
    assert "Each write uses its persisted idempotency key" in value
    assert "HTTP 409 produces an explicit conflict state" in value
    assert "never changes `expected_version`" in value


def test_service_worker_never_caches_api_and_manual_sync_is_required():
    value = text()
    assert "never intercepts or caches `/api`" in value
    assert "Manual synchronization is always available" in value
    assert "must not silently submit queued writes" in value
