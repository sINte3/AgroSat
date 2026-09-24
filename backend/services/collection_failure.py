"""One vocabulary for why a satellite collection cycle failed.

The database owns the durable contract. ``ck_collection_runs_failure`` bounds
``satellite_collection_runs.failure_category`` to eight labels, and a write
outside them is rejected outright.

Before this module there were three vocabularies. The collector classified
failures with a wider set of its own, the readiness reader accepted a third
set, and the constraint accepted neither in full:

* the collector could emit ``cloud``, ``partial`` and ``cancelled``; the
  database rejects all three;
* the database accepts ``timeout`` and ``quality``; the collector never
  emitted either;
* the readiness reader accepted the collector's labels, so it disagreed with
  the database in the opposite direction.

``partial`` is what an ordinary exit code 1 produces — a cycle where some
fields failed — so the common case was the broken one. The 2026-09-23
production cycle ended exactly there: ``classify_failure`` returned
``partial``, ``finish_apply_run``'s write raised ``CheckViolation``, the
aborted transaction then rejected the ``pg_advisory_unlock`` in that
function's ``finally``, and the unlock error replaced the real one. The run
row was left ``running``.

Everything that names a failure category now resolves it here.
"""

from __future__ import annotations


#: The database's vocabulary, and therefore the system's. Kept in lockstep
#: with ``ck_collection_runs_failure`` by a PostgreSQL test that reads the
#: live constraint; widening this set requires a migration, not an edit.
RUN_FAILURE_CATEGORIES = frozenset(
    {
        "auth",
        "quota",
        "network",
        "timeout",
        "quality",
        "lock_contention",
        "contract",
        "operational",
    }
)

#: Every label any producer may hand us, mapped onto the durable vocabulary.
#: The collector's provider layer speaks the left-hand side; the database only
#: ever sees the right.
_CANONICAL_BY_LABEL = {
    # identity
    "auth": "auth",
    "quota": "quota",
    "network": "network",
    "timeout": "timeout",
    "quality": "quality",
    "lock_contention": "lock_contention",
    "contract": "contract",
    "operational": "operational",
    # provider-level names for the same conditions
    "authentication": "auth",
    "connection": "network",
    "dns": "network",
    "provider_error": "operational",
    # conditions the database expresses differently. Cloud cover and a failed
    # quality gate are the same outcome to a reader: the scene was not usable.
    "cloud": "quality",
    "cloud_blocked": "quality",
    "quality_blocked": "quality",
    # a run that partly succeeded, and one an operator stopped, are both
    # operational outcomes here. The run's `status` column already separates
    # 'degraded' from 'failed', so no information is lost by folding them.
    "partial": "operational",
    "cancelled": "operational",
}


def canonical_failure_category(label: str | None) -> str | None:
    """Return the durable category for ``label``.

    ``None`` stays ``None``: a successful cycle has no category. Anything the
    mapping does not recognise degrades to ``operational`` rather than
    reaching the database as itself — an unrecognised label is a reason to
    record the failure imprecisely, never a reason to fail the write that
    records it and lose the cycle's outcome entirely.
    """
    if label is None:
        return None
    normalized = str(label).strip().lower()
    if not normalized:
        return None
    return _CANONICAL_BY_LABEL.get(normalized, "operational")
