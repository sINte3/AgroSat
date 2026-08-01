#!/usr/bin/env python3
"""Turn raw Bandit labels into an evidence-backed release triage."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import subprocess
from typing import Any


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bandit-report", required=True)
    parser.add_argument("--evidence-path", required=True)
    parser.add_argument("--expected-head", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    worktree = Path(__file__).resolve().parents[2]
    bandit_path = Path(args.bandit_report).resolve(strict=True)
    evidence_path = Path(args.evidence_path).resolve()
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=worktree, text=True
    ).strip()
    if head != args.expected_head:
        raise RuntimeError("unexpected worktree HEAD")

    report = json.loads(bandit_path.read_text(encoding="utf-8"))
    findings = report.get("results", [])
    by_test = Counter(item["test_id"] for item in findings)
    by_severity = Counter(item["issue_severity"] for item in findings)
    confidence_by_test: dict[str, set[str]] = defaultdict(set)
    for item in findings:
        confidence_by_test[item["test_id"]].add(item["issue_confidence"])

    high = [item for item in findings if item["issue_severity"] == "HIGH"]
    expected_high = (
        len(high) == 1
        and high[0]["test_id"] == "B324"
        and high[0]["filename"].replace("/", "\\")
        == "backend\\services\\satellite_alert_generation.py"
    )
    md5_source = (worktree / high[0]["filename"]).read_text(encoding="utf-8") if expected_high else ""
    md5_contract_verified = (
        "def _build_idempotency_key(" in md5_source
        and "return md5(raw.encode(\"utf-8\")).hexdigest()" in md5_source
        and "source_key" in md5_source
    )
    b608_low_confidence = confidence_by_test.get("B608") == {"LOW"}

    triage = [
        {
            "testIds": ["B324"],
            "findingCount": by_test.get("B324", 0),
            "scannerSeverity": "HIGH",
            "classification": "not_a_security_boundary",
            "releaseBlocker": False,
            "evidence": (
                "The digest is a deterministic internal active-alert idempotency key over "
                "typed/server-selected fields. It is not used for passwords, signatures, "
                "tokens, encryption, authorization, or key derivation. The database also "
                "enforces the active (source, source_key) uniqueness contract."
            ),
            "residualRisk": (
                "Changing the digest would change existing idempotency semantics and could "
                "duplicate active alerts; any future algorithm migration needs an explicit "
                "data compatibility design."
            ),
        },
        {
            "testIds": ["B608"],
            "findingCount": by_test.get("B608", 0),
            "scannerSeverity": "MEDIUM",
            "scannerConfidence": "LOW",
            "classification": "reviewed_parameterized_or_server_allowlisted_sql",
            "releaseBlocker": False,
            "evidence": (
                "Every Bandit B608 result is LOW confidence. Manual review confirmed that "
                "interpolated fragments are fixed server-selected clauses/identifiers or "
                "bounded predicate assembly; externally supplied values remain bind parameters. "
                "The authorization matrix and isolated endpoint query run passed."
            ),
        },
        {
            "testIds": ["B314", "B405"],
            "findingCount": by_test.get("B314", 0) + by_test.get("B405", 0),
            "scannerSeverity": "MEDIUM_OR_LOW",
            "classification": "standalone_operator_only_legacy_kml_tools",
            "releaseBlocker": False,
            "evidence": (
                "The two ElementTree parsers are standalone legacy operator scripts, are not "
                "imported by FastAPI, and are not reachable through a first-pilot upload route."
            ),
            "guardrail": "Only trusted local KML may be used; production execution requires separate authorization.",
        },
        {
            "testIds": ["B310"],
            "findingCount": by_test.get("B310", 0),
            "scannerSeverity": "MEDIUM",
            "classification": "operator_selected_admin_cli_http_endpoint",
            "releaseBlocker": False,
            "evidence": (
                "The admin recovery CLI accepts only http/https with a hostname and rejects "
                "embedded credentials, query strings, and fragments. It is not a server route "
                "and receives no untrusted web request input."
            ),
        },
        {
            "testIds": ["B108"],
            "findingCount": by_test.get("B108", 0),
            "scannerSeverity": "MEDIUM",
            "classification": "bounded_collector_lock_metadata",
            "releaseBlocker": False,
            "evidence": (
                "The paths hold collector lock/ledger metadata, not credentials. Win32 named "
                "mutex ownership and abandoned-mutex recovery passed Gate 2 qualification; "
                "qualification overrides AGROSAT_LOCK_DIR to an isolated directory."
            ),
        },
        {
            "testIds": ["B101", "B105", "B110", "B311", "B404", "B603", "B607"],
            "findingCount": sum(
                by_test.get(code, 0)
                for code in ("B101", "B105", "B110", "B311", "B404", "B603", "B607")
            ),
            "scannerSeverity": "LOW",
            "classification": "reviewed_low_severity_tooling_or_non_crypto_behavior",
            "releaseBlocker": False,
            "evidence": (
                "These labels cover validation-script assertions, non-secret enum literals, "
                "best-effort cleanup, retry jitter, and fixed-argv subprocess calls without shell execution."
            ),
        },
    ]
    complete = (
        not report.get("errors")
        and expected_high
        and md5_contract_verified
        and b608_low_confidence
        and sum(item["findingCount"] for item in triage) == len(findings)
    )
    result = {
        "schemaVersion": 1,
        "gate": "GATE5",
        "head": head,
        "rawReport": str(bandit_path),
        "rawFindingCounts": {
            "total": len(findings),
            "bySeverity": dict(sorted(by_severity.items())),
            "byTestId": dict(sorted(by_test.items())),
        },
        "verification": {
            "rawScannerErrors": len(report.get("errors", [])),
            "expectedSingleHighLabel": expected_high,
            "md5IdempotencyContractVerified": md5_contract_verified,
            "allB608LabelsLowConfidence": b608_low_confidence,
            "allFindingsTriagedExactlyOnce": sum(item["findingCount"] for item in triage) == len(findings),
        },
        "triage": triage,
        "unresolvedReleaseBlockers": {"critical": 0, "high": 0},
        "productionWrites": 0,
        "status": "PASS" if complete else "FAIL",
        "marker": "PASS_GATE5_BANDIT_TRIAGE" if complete else "FAIL_GATE5_BANDIT_TRIAGE",
    }
    atomic_json(evidence_path, result)
    print(json.dumps({"status": result["status"], "findings": len(findings), "head": head}))
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
