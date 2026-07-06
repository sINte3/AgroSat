#!/usr/bin/env python3
"""
CLI for satellite alert candidate generation and protected apply mode.

Read-only by default. Persistent writes require explicit --apply with
safe scope guards.

Usage:
  python -m scripts.generate_satellite_alerts --help
  python -m scripts.generate_satellite_alerts --dry-run
  python -m scripts.generate_satellite_alerts --field-id 42
  python -m scripts.generate_satellite_alerts --enterprise-id 1 --min-severity high
  python -m scripts.generate_satellite_alerts --apply --limit 5 --rollback
  python -m scripts.generate_satellite_alerts --apply --field-id 42

Options:
  --dry-run          Generate candidates, print summary, no DB writes (default).
  --apply            Enable persistent alert creation.
  --rollback         Perform apply inside transaction then rollback (validation).
  --field-id N       Filter to a specific field.
  --enterprise-id N  Filter to a specific enterprise.
  --min-severity S   Minimum severity (low, medium, high, critical).
  --limit N          Max candidates (1-1000, default 100).
  --fresh-days N     Max age in days for 'fresh' status (default 10).
  --include-low      Include low-severity reasons (can be noisy).
  --json             Output raw JSON instead of formatted table.

Apply guards:
  --apply requires at least one of: --field-id, --enterprise-id, --limit < 1000
  --apply with --limit >= 1000 and no --field-id/--enterprise-id is rejected.
"""
import argparse
import json
import sys
import logging

from database import SessionLocal
from services.satellite_alert_generation import (
    generate_alert_candidates,
    apply_alert_candidates,
    MAX_CANDIDATES_LIMIT,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def _validate_apply_guards(args: argparse.Namespace) -> str | None:
    """
    Validate --apply guards.

    Returns error message string if rejected, None if allowed.
    """
    has_field_id = args.field_id is not None
    has_enterprise_id = args.enterprise_id is not None
    has_scope = has_field_id or has_enterprise_id

    # --apply without any scope guard
    if not has_scope and args.limit >= 1000:
        return (
            "--apply with --limit >= 1000 requires --field-id "
            "or --enterprise-id to limit scope."
        )

    if not has_scope and args.limit > 100:
        return (
            "--apply with --limit > 100 requires --field-id "
            "or --enterprise-id to limit scope."
        )

    # --apply with no limit cap and no scope — deprecated path,
    # let through if limit is reasonable but log a warning
    return None


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate satellite alert candidates from agronomic risk facts. "
            "Protected apply mode requires explicit --apply flag."
        ),
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
        help="Enable persistent alert creation (protected mode).",
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="When used with --apply, roll back transaction (validation).",
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

    # Validate and cap limit
    if args.limit < 1 or args.limit > MAX_CANDIDATES_LIMIT:
        print(f"ERROR: --limit must be between 1 and {MAX_CANDIDATES_LIMIT}.", file=sys.stderr)
        sys.exit(1)

    # Determine mode
    is_apply = args.apply
    is_dry_run = not is_apply
    is_rollback = args.rollback

    # Validate apply guards
    if is_apply:
        guard_error = _validate_apply_guards(args)
        if guard_error:
            print(f"ERROR: {guard_error}", file=sys.stderr)
            sys.exit(1)

    # Open DB session
    try:
        db = SessionLocal()
    except Exception as e:
        print(f"ERROR: Cannot connect to database: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        # Generate candidates (shared path — always read-only)
        result = generate_alert_candidates(
            db=db,
            fresh_days=args.fresh_days,
            limit=args.limit,
            enterprise_id=args.enterprise_id,
            field_id=args.field_id,
            min_severity=args.min_severity,
            include_low=args.include_low,
        )

        if is_dry_run:
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                _print_summary(result)
            return

        # Apply mode
        candidates = result.get("candidates", [])
        apply_result = apply_alert_candidates(
            db=db,
            candidates=candidates,
            rollback=is_rollback,
        )

        # Commit if non-rollback apply succeeded
        if not is_rollback and apply_result.get("committed"):
            db.commit()

        if args.json:
            print(json.dumps(apply_result, indent=2, default=str))
        else:
            _print_apply_summary(apply_result, result)
    finally:
        db.close()


def _print_summary(result: dict):
    """Print a human-readable summary (dry-run)."""
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


def _print_apply_summary(apply_result: dict, candidates_result: dict):
    """Print a human-readable summary (apply mode)."""
    summary = apply_result["summary"]
    candidates_summary = candidates_result.get("summary", {})
    is_rollback = apply_result.get("rollback", False)
    mode_label = "APPLY (rollback)" if is_rollback else "APPLY (persistent)"

    print("=" * 60)
    print(f"SATELLITE ALERT {mode_label}")
    print("=" * 60)
    print(f"  Generated at:           {apply_result['generated_at']}")
    print(f"  Mode:                   {apply_result['mode']}")
    print(f"  Rollback:               {is_rollback}")
    print(f"  Risk fields evaluated:  {candidates_summary.get('risk_fields_evaluated', '?')}")
    print(f"  Candidates total:       {summary['candidates_total']}")
    print(f"  Inserted:               {summary['inserted']}")
    print(f"  Skipped existing:       {summary['skipped_existing']}")
    print(f"  Blocked:                {summary['blocked']}")
    print()

    results = apply_result.get("results", [])
    if results:
        action_groups: dict[str, list] = {}
        for r in results:
            act = r.get("action", "unknown")
            action_groups.setdefault(act, []).append(r)

        for action, items in sorted(action_groups.items()):
            print(f"  {action.upper()} ({len(items)}):")
            for item in items[:10]:
                print(
                    f"    field={item['field_id']} "
                    f"type={item.get('alert_type', '?')} "
                    f"source_key={item.get('source_key', '?')[:16]}..."
                )
            if len(items) > 10:
                print(f"    ... and {len(items) - 10} more")

    limitations = apply_result.get("limitations", [])
    if limitations:
        print()
        print("  Limitations:")
        for lim in limitations:
            print(f"    - {lim}")

    committed = apply_result.get("committed", False)
    rolled_back = apply_result.get("rolled_back", False)
    print()
    if rolled_back:
        print("  Transaction rolled back — no persistent changes.")
    elif committed:
        print("  Persistent writes committed.")
    else:
        print("  Status unknown — no explicit commit or rollback recorded.")
    print("=" * 60)


if __name__ == "__main__":
    main()
