"""Static safety contracts for TASK_220 database, API and collector integration."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text('utf-8')


def test_single_next_migration_and_tenant_bound_schema():
    migration = read('alembic/versions/0015_closed_loop_agronomy.py')
    assert 'revision = "0015_closed_loop_agronomy"' in migration
    assert 'down_revision = "0014_autonomous_satellite_monitoring"' in migration
    for table in ('agronomy_plans','agronomy_work_items','agronomy_verifications','agronomy_events'):
        assert f'CREATE TABLE {table}' in migration
    for contract in ('fk_agronomy_plan_inspection','fk_agronomy_work_plan','uq_agronomy_event_command','uq_agronomy_verification_identity'):
        assert contract in migration


def test_api_is_typed_tenant_service_and_no_web_scheduler():
    api = read('api/closed_loop_agronomy.py')
    main = read('main.py')
    assert 'closed_loop_agronomy_router' in main
    assert 'Idempotency-Key' in api and 'get_current_active_user' in api
    assert 'APScheduler' not in main and 'collect_satellite' not in main


def test_collector_reconciles_boundedly_without_masking_counters():
    collector = read('scripts/collect_satellite.py')
    service = read('services/closed_loop_agronomy.py')
    assert 'reconcile_pending(SessionLocal, limit=100)' in collector
    assert 'FOR UPDATE SKIP LOCKED' in service
    for counter in ('eligible','improved','unchanged','worsened','pending_quality_provider','conflicts','failures'):
        assert counter in service


def test_policy_is_deterministic_human_reviewed_and_has_no_llm_dependency():
    policy = read('services/agronomy_policy.py')
    assert "POLICY = \"r3-f-v1\"" in policy
    assert '"requires_approval": True' in policy
    assert 'MATERIAL_DELTA = 0.05' in policy and 'WAIT_DAYS = 7' in policy
    assert 'openai' not in policy.lower() and 'anthropic' not in policy.lower()
