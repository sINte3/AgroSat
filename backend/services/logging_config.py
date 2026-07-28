"""Sanitizing JSON logging for application processes."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from typing import Any


MAX_MESSAGE_LENGTH = 4000


def sanitize_log_text(value: Any) -> str:
    text = str(value)[:MAX_MESSAGE_LENGTH]
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*)(\S+)",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)(token|password|secret|api[_-]?key)\s*[:=]\s*[^\s,]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(
        r"\b\w+(?:\+\w+)?://[^\s@]+@[^\s]+",
        "[REDACTED_URL]",
        text,
    )
    text = re.sub(
        r"(?i)(?:\\\\[?.]\\)?[a-z]:\\users\\[^\\]+",
        "[REDACTED_USER_PATH]",
        text,
    )
    return text


class SanitizingJsonFormatter(logging.Formatter):
    def __init__(self, release_revision: str):
        super().__init__()
        self.release_revision = re.sub(
            r"[^A-Za-z0-9._-]",
            "",
            str(release_revision or "unknown"),
        )[:128] or "unknown"

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name[:160],
            "event": sanitize_log_text(record.getMessage()),
            "release_revision": self.release_revision,
        }
        if record.exc_info:
            payload["exception_type"] = type(record.exc_info[1]).__name__
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_logging(*, level: int, release_revision: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SanitizingJsonFormatter(release_revision))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
