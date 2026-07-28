"""Redis remains an optional, bounded cache rather than a source of truth."""

import ast
import json
from pathlib import Path
from unittest.mock import Mock, patch

from services import cache


def reset_cache_state():
    cache._redis = None
    cache.CACHE_AVAILABLE = False
    cache._retry_after_monotonic = 0.0


def test_module_has_no_import_time_redis_call():
    source = Path(cache.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level_calls = [
        node
        for statement in tree.body
        for node in ast.walk(statement)
        if isinstance(node, ast.Call)
        and not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert not any(
        isinstance(node.func, ast.Attribute)
        and node.func.attr in {"from_url", "ping"}
        for node in top_level_calls
    )


def test_first_cache_read_connects_lazily():
    reset_cache_state()
    client = Mock()
    client.get.return_value = json.dumps({"value": 4})
    with patch("services.cache.redis.from_url", return_value=client) as from_url:
        assert cache.cache_get("tenant:17:dashboard") == {"value": 4}

    from_url.assert_called_once()
    client.ping.assert_called_once_with()
    client.get.assert_called_once_with("tenant:17:dashboard")


def test_redis_outage_bypasses_cache_and_uses_backoff():
    reset_cache_state()
    with patch(
        "services.cache.redis.from_url",
        side_effect=RuntimeError("unavailable"),
    ) as from_url:
        assert cache.cache_get("tenant:17:dashboard") is None
        assert cache.cache_get("tenant:17:dashboard") is None

    from_url.assert_called_once()
    assert cache.CACHE_AVAILABLE is False


def test_cache_operation_failure_cannot_be_reported_as_success():
    reset_cache_state()
    client = Mock()
    client.setex.side_effect = RuntimeError("offline")
    with patch.object(cache, "_redis", client), patch.object(
        cache,
        "CACHE_AVAILABLE",
        True,
    ):
        assert cache.cache_set("tenant:17:value", {"value": 1}, 60) is False


def test_ttl_and_key_bounds_are_enforced():
    reset_cache_state()
    assert cache.cache_set("bad key", {"value": 1}, 60) is False
    assert cache.cache_set("valid:key", {"value": 1}, 0) is False
    assert (
        cache.cache_set(
            "valid:key",
            {"value": 1},
            cache.MAX_CACHE_TTL_SECONDS + 1,
        )
        is False
    )


def test_pattern_invalidation_uses_scan_and_never_keys_or_flush():
    reset_cache_state()
    client = Mock()
    client.scan.side_effect = [
        (7, ["fields:1", "fields:2"]),
        (0, ["fields:3"]),
    ]
    with patch.object(cache, "_redis", client), patch.object(
        cache,
        "CACHE_AVAILABLE",
        True,
    ):
        assert cache.cache_delete_pattern("fields:*") is True

    assert client.scan.call_count == 2
    assert client.delete.call_count == 2
    client.keys.assert_not_called()
    client.flushall.assert_not_called()
    client.flushdb.assert_not_called()


def test_pattern_invalidation_stops_at_bounded_scan_count():
    reset_cache_state()
    client = Mock()
    client.scan.return_value = (1, [])
    with patch.object(cache, "_redis", client), patch.object(
        cache,
        "CACHE_AVAILABLE",
        True,
    ):
        assert cache.cache_delete_pattern("fields:*") is False

    assert client.scan.call_count == cache.MAX_PATTERN_SCAN_CALLS


def test_mutation_sources_commit_before_cache_invalidation():
    backend = Path(__file__).resolve().parents[1]
    alerts = (backend / "api" / "alerts.py").read_text(encoding="utf-8")
    fields = (backend / "api" / "fields.py").read_text(encoding="utf-8")
    assert alerts.index("db.commit()", alerts.index("def acknowledge_alert")) < alerts.index(
        'cache_delete_pattern("alerts:*")',
        alerts.index("def acknowledge_alert"),
    )
    for function in ("def create_field", "def update_field", "def set_field_season"):
        start = fields.index(function)
        commit = fields.index("db.commit()", start)
        invalidate = fields.index("_invalidate_field_caches()", start)
        assert commit < invalidate
