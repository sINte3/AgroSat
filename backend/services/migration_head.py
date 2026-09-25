"""The database schema revision the running code requires.

A release is immutable, and the Alembic migration graph shipped inside it has
exactly one head: the only schema revision this code was built and tested
against. Readiness compares the live database with that head.

This module is the one place that resolves it. It reads the graph through
Alembic's own ``ScriptDirectory``, from the ``alembic.ini`` next to this code,
so it resolves exactly what ``alembic upgrade head`` would apply. It never
takes the revision from a constant, from the release SHA or from Git, and it
never touches a database.

Resolving loads every migration module once, so the result is cached for the
life of the process: the code under a running process does not change. A
graph that cannot be resolved (no head, several heads, or unreadable) is
reported as a problem instead of raising, and is retried at most once per
``UNRESOLVED_RETRY_SECONDS``: a transient read error heals without a restart,
and a genuinely broken graph still costs one read per interval, not one per
request.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
import threading
import time
from typing import Callable


ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
REVISION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
UNRESOLVED_RETRY_SECONDS = 60
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MigrationHead:
    """What the code's migration graph says the database must be at.

    ``revision`` is set only when the graph has exactly one well-formed head.
    Otherwise ``problem`` names why, as one of ``no_head``,
    ``multiple_heads`` or ``unreadable``. ``known_revisions`` is every
    revision in a resolved graph, which lets readiness tell a database that is
    merely behind from one this code has never heard of.
    """

    revision: str | None
    problem: str | None = None
    known_revisions: frozenset[str] = frozenset()

    @property
    def resolved(self) -> bool:
        return self.revision is not None


def resolve_migration_head(alembic_ini: Path = ALEMBIC_INI) -> MigrationHead:
    """Read the single head of the migration graph configured by ``alembic_ini``.

    Never raises. The result carries no filesystem path or exception text.
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(alembic_ini))
        # alembic.ini sets prepend_sys_path for the CLI; resolving the graph
        # inside a web process must not rewrite that process's import path.
        config.set_main_option("prepend_sys_path", "")
        script = ScriptDirectory.from_config(config)
        heads = tuple(script.get_heads())
        known = (
            frozenset(item.revision for item in script.walk_revisions())
            if len(heads) == 1
            else frozenset()
        )
    except Exception:
        logger.warning("migration head unresolved: unreadable", exc_info=True)
        return MigrationHead(None, "unreadable")
    if not heads:
        logger.warning("migration head unresolved: no_head")
        return MigrationHead(None, "no_head")
    if len(heads) > 1:
        logger.warning("migration head unresolved: multiple_heads")
        return MigrationHead(None, "multiple_heads")
    head = heads[0]
    if not isinstance(head, str) or not REVISION_PATTERN.fullmatch(head):
        logger.warning("migration head unresolved: unreadable")
        return MigrationHead(None, "unreadable")
    return MigrationHead(head, None, known)


_lock = threading.Lock()
_cache: tuple[MigrationHead, float] | None = None


def _usable(cached: tuple[MigrationHead, float] | None, now: float) -> bool:
    return cached is not None and (
        cached[0].resolved or now - cached[1] < UNRESOLVED_RETRY_SECONDS
    )


def expected_migration_head(
    *,
    clock: Callable[[], float] = time.monotonic,
) -> MigrationHead:
    """The head the running code requires, resolved once per process."""
    global _cache
    cached = _cache
    if _usable(cached, clock()):
        return cached[0]
    with _lock:
        cached = _cache
        if not _usable(cached, clock()):
            cached = (resolve_migration_head(), clock())
            _cache = cached
        return cached[0]
