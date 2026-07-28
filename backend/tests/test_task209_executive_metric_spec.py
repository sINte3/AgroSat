from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs" / "TASK_209_EXECUTIVE_ACCOUNTABILITY_METRICS.md"


def test_executive_metric_spec_is_current_and_versioned():
    text = SPEC.read_text(encoding="utf-8")
    assert "task209_executive_v1" in text
    assert "Asia/Tashkent" in text
    assert "at most 366 inclusive calendar days" in text


def test_executive_metric_spec_defines_required_questions():
    text = SPEC.read_text(encoding="utf-8")
    required = {
        "attention_fields_now",
        "unassigned_inspections",
        "overdue_inspections",
        "overdue_actions",
        "awaiting_verification",
        "attention_signal_to_inspection_hours",
        "inspection_to_action_hours",
        "action_to_close_hours",
        "improved",
        "unchanged",
        "worsened",
        "insufficient_data",
        "attention_stale_fields",
        "attention_low_confidence_fields",
    }
    assert required <= set(word.strip("`:,;.") for word in text.split())


def test_executive_metric_spec_is_honest_about_attention_duration():
    text = SPEC.read_text(encoding="utf-8")
    normalized = " ".join(text.lower().split())
    assert "coarse proxy" in text
    assert "no durable `attention_entered_at`" in text
    assert "does not prove agronomic causality" in normalized


def test_executive_metric_spec_requires_server_tenant_scope_and_bounded_lists():
    text = SPEC.read_text(encoding="utf-8")
    normalized = " ".join(text.lower().split())
    assert "manager-supplied foreign `enterprise_id` is rejected with 403" in text
    assert "one total query and one bounded page query" in text
    assert "`limit` from 1 through 200" in text
    assert "does not calculate tenant-wide totals in the browser" in normalized


def test_executive_metric_spec_records_database_blocker():
    text = SPEC.read_text(encoding="utf-8")
    assert "depends on Alembic revision `0006`" in text
    assert "Until B-001 is resolved" in text
    assert "must not be reported as live database validation" in text
