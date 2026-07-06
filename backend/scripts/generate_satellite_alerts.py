#!/usr/bin/env python3
"""
CLI for satellite alert candidate generation.

Read-only by default. Dry-run only — no DB writes.

Usage:
  python -m scripts.generate_satellite_alerts --help
  python -m scripts.generate_satellite_alerts --dry-run
  python -m scripts.generate_satellite_alerts --field-id 42
  python -m scripts.generate_satellite_alerts --enterprise-id 1 --min-severity high

Options:
  --dry-run          Generate candidates, print summary, no DB writes (default).
  --field-id N       Filter to a specific field.
  --enterprise-id N  Filter to a specific enterprise.
  --min-severity S   Minimum severity (low, medium, high, critical).
  --limit N          Max candidates (1-1000, default 100).
  --fresh-days N     Max age in days for 'fresh' status (default 10).
  --include-low      Include low-severity reasons (can be noisy).
  --json             Output raw JSON instead of formatted table.

No --apply flag: persistent alert creation is deferred until alerts table
has a dedicated idempotency column (source_key).
"""
import argparse
import json
import sys
import logging

from database import SessionLocal
from services.satellite_alert_generation import generate_alert_candidates

logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Generate satellite alert candidates from agronomic risk facts (dry-run only).",
    )

    # Mode flags
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry-run mode (default, implied if no --apply).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="NOT IMPLEMENTED. Persistent apply requires schema migration.",
    )

    # Filter options
    parser.add_argument("--field-id", type=int, default=None, help="Filter to a specific field.")
    parser.add_argument(
        "--enterprise-id", type=int, default=None, help="Filter to a specific enterprise."
    )
    parser.add_argument(
        "--min-severity",
        type=str,
        default=None,
        choices=["low", "medium", "high", "critical"],
        help="Minimum severity to include.",
    )
    parser.add_argument(
        "--limit", type=int, default=100, help="Max candidates (1-1000)."
    )
    parser.add_argument("--fresh-days", type=int, default=10, help="Max age in days for fresh data.")
    parser.add_argument("--include-low", action="store_true", help="Include low-severity reasons.")

    # Output format
    parser.add_argument("--json", action="store_true", help="Output raw JSON.")

    args = parser.parse_args()

    # Reject --apply
    if args.apply:
        print("ERROR: --apply is not implemented.", file=sys.stderr)
        print(
            "Persistent alert creation requires a migration to add "
            "a 'source_key' idempotency column to the alerts table.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate and cap limit
    if args.limit < 1 or args.limit > 1000:
        print("ERROR: --limit must be between 1 and 1000.", file=sys.stderr)
        sys.exit(1)

    # Open DB session
    try:
        db = SessionLocal()
    except Exception as e:
        print(f"ERROR: Cannot connect to database: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        result = generate_alert_candidates(
            db=db,
            fresh_days=args.fresh_days,
            limit=args.limit,
            enterprise_id=args.enterprise_id,
            field_id=args.field_id,
            min_severity=args.min_severity,
            include_low=args.include_low,
        )

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_summary(result)
    finally:
        db.close()


def _print_summary(result: dict):
    """Print a human-readable summary."""
    summary = result["summary"]
    print("=" * 60)
    print("SATELLITE ALERT CANDIDATES (dry-run)")
    print("=" * 60)
    print(f"  Generated at:         {result['generated_at']}")
    print(f"  Mode:                 {result['mode']}")
    print(f"  Risk fields evaluated: {summary['risk_fields_evaluated']}")
    print(f"  Total candidates:      {summary['candidates_total']}")
    print(f"  Would insert:          {summary['would_insert']}")
    print(f"  Would skip existing:   {summary['would_skip_existing']}")
    print(f"  Blocked:              {summary['blocked']}")
    print()

    by_severity = result.get("by_severity", {})
    if by_severity:
        print("  By severity:")
        for sev in ["low", "medium", "high", "critical"]:
            count = by_severity.get(sev, 0)
            if count:
                print(f"    {sev:12s} {count}")

    by_type = result.get("by_alert_type", {})
    if by_type:
        print()
        print("  By alert type:")
        for t, count in sorted(by_type.items()):
            if count:
                print(f"    {t:40s} {count}")

    candidates = result.get("candidates", [])
    if candidates:
        print()
        print(f"  Candidates (first {min(len(candidates), 10)}):")
        for c in candidates[:10]:
            print(
                f"    field={c['field_id']} "
                f"type={c['alert_type']:35s} "
                f"severity={c['severity']:10s} "
                f"reason={c['reason_code']}"
            )
        if len(candidates) > 10:
            print(f"    ... and {len(candidates) - 10} more")

    limitations = result.get("limitations", [])
    if limitations:
        print()
        print("  Limitations:")
        for lim in limitations:
            print(f"    - {lim}")

    print()
    print("  No DB writes performed.")
    print("=" * 60)


if __name__ == "__main__":
    main()
