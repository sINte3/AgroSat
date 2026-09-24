#!/usr/bin/env python3
"""Read-only preview of the canonical observation-signal producer (TASK_225).

Prints what the collector's detection step would record on its next apply
cycle, against the configured database, without writing anything: the
session runs in a READ ONLY transaction and is rolled back. The decision is
the one the collector uses (services.autonomous_monitoring
._observation_assessments), so the preview cannot disagree with the write.

Run it before enabling OBSERVATION_DETECTION_ENABLED for a deployment:

    python scripts/preview_observation_candidates.py [--as-of YYYY-MM-DD] [--limit 50]

Exit codes: 0 preview printed, 2 invalid arguments.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, time, timezone
import json
from pathlib import Path
import sys


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--as-of", type=date.fromisoformat, default=None,
                        help="Evaluate as of this UTC date (default: now).")
    parser.add_argument("--limit", type=int, default=50,
                        help="Candidates to list in full (1..500); counters always cover all.")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 500:
        parser.error("--limit must be between 1 and 500")
    return args


def build_report(session, *, as_of: datetime, limit: int) -> dict:
    from sqlalchemy import text

    from services.autonomous_anomaly_engine import RulePolicy
    from services.autonomous_monitoring import preview_observation_candidates

    session.execute(text("SET TRANSACTION READ ONLY"))
    active_fields = int(session.execute(text(
        "SELECT count(*) FROM fields WHERE is_active=true"
    )).scalar_one())
    preview = preview_observation_candidates(session, as_of=as_of)
    candidates = preview["candidates"]
    automatic_fields = {item["field_id"] for item in candidates if item["automatic"]}
    guard = RulePolicy().max_spike_fraction
    return {
        "mode": "read-only preview",
        "as_of": as_of.isoformat(),
        "active_fields": active_fields,
        "counters": preview["counters"],
        "by_severity": dict(sorted(Counter(item["severity"] for item in candidates).items())),
        "automatic_eligible": len(automatic_fields),
        "spike_guard_would_trigger": bool(active_fields)
        and len(automatic_fields) / active_fields > guard,
        "candidates": candidates[:limit],
    }


def main(argv: list[str] | None = None, *, session_factory=None) -> int:
    args = parse_args(argv)
    as_of = (datetime.combine(args.as_of, time(12), tzinfo=timezone.utc)
             if args.as_of else datetime.now(timezone.utc))
    if session_factory is None:
        from database import SessionLocal as session_factory
    session = session_factory()
    try:
        report = build_report(session, as_of=as_of, limit=args.limit)
    finally:
        session.rollback()
        session.close()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
