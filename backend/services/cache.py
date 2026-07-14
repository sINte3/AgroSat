"""
Redis cache service for AgroSat backend.
Reduces repeated queries to Supabase (London) from Bukhara.
"""
import json
import logging
import base64
from typing import Optional, Any
import redis
from config import settings

logger = logging.getLogger(__name__)

try:
    _redis = redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=2)
    _redis.ping()
    CACHE_AVAILABLE = True
    logger.info("✅ Redis cache connected")
except Exception as e:
    _redis = None
    CACHE_AVAILABLE = False
    logger.warning(f"⚠️ Redis not available, caching disabled: {e}")


def cache_get(key: str) -> Optional[Any]:
    if not CACHE_AVAILABLE or not _redis:
        return None
    try:
        val = _redis.get(key)
        return json.loads(val) if val else None
    except Exception:
        return None


def cache_set(key: str, value: Any, ttl_seconds: int = 120) -> bool:
    if not CACHE_AVAILABLE or not _redis:
        return False
    try:
        _redis.setex(key, ttl_seconds, json.dumps(value, default=str))
        return True
    except Exception:
        return False


def cache_get_binary(key: str) -> Optional[bytes]:
    """Return base64-encoded binary data safely with decode_responses=True Redis."""
    if not CACHE_AVAILABLE or not _redis:
        return None
    try:
        value = _redis.get(key)
        if not value:
            return None
        return base64.b64decode(value.encode("ascii"), validate=True)
    except Exception:
        return None


def cache_set_binary(key: str, value: bytes, ttl_seconds: int) -> bool:
    """Store binary data as ASCII base64 without changing existing JSON cache behavior."""
    if not CACHE_AVAILABLE or not _redis:
        return False
    try:
        _redis.setex(key, ttl_seconds, base64.b64encode(value).decode("ascii"))
        return True
    except Exception:
        return False


def cache_delete(key: str) -> None:
    if CACHE_AVAILABLE and _redis:
        try:
            _redis.delete(key)
        except Exception:
            pass


def cache_delete_pattern(pattern: str) -> None:
    """Delete all keys matching pattern, e.g. 'alerts:*'"""
    if CACHE_AVAILABLE and _redis:
        try:
            keys = _redis.keys(pattern)
            if keys:
                _redis.delete(*keys)
        except Exception:
            pass
