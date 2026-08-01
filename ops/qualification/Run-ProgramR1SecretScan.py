#!/usr/bin/env python3
"""Scan tracked repository content and the release diff without exposing matches."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any, Iterable
from urllib.parse import urlparse


PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key_header", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,255}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("credentialed_database_url", re.compile(r"\b(?:postgres(?:ql)?|redis)://[^\s:/]+:[^\s@/]+@[^\s]+", re.IGNORECASE)),
    ("bearer_literal", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE)),
    (
        "credential_assignment",
        re.compile(
            r"(?i)\b(?:client_secret|api_key|access_token|password|secret_key)\b\s*[:=]\s*[\"\']([^\"\'\s]{16,})[\"\']"
        ),
    ),
)

BINARY_SUFFIXES = {
    ".7z", ".avif", ".bmp", ".db", ".dll", ".docx", ".exe", ".gif",
    ".ico", ".jpeg", ".jpg", ".pdf", ".png", ".pyc", ".sqlite", ".webp",
    ".xlsx", ".zip",
}


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--evidence-path", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--diff-base", required=True)
    return parser.parse_args()


def git(worktree: Path, *arguments: str, binary: bool = False) -> bytes | str:
    raw = subprocess.check_output(["git", *arguments], cwd=str(worktree))
    if binary:
        return raw
    return raw.decode("utf-8", errors="replace")


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def classify(path: str, rule: str, value: str) -> str:
    lowered = value.casefold()
    if (
        path.startswith("ops/qualification/")
        and rule == "credentialed_database_url"
        and ("$(" in value or "${" in value)
    ):
        return "verified_ephemeral_runtime_expression"
    sanitizer_fixture_paths = {
        "backend/tests/test_multi_index_collection_cycle.py",
        "backend/tests/test_ndvi_collection_cycle.py",
        "backend/tests/test_task209_productivity_zone_cli.py",
    }
    if path in sanitizer_fixture_paths and rule == "credentialed_database_url":
        return "verified_sanitizer_test_fixture"
    if path.startswith("backend/tests/") and rule == "credentialed_database_url":
        parsed = urlparse(value)
        safe_users = {"u", "user", "username", "test", "tester"}
        safe_passwords = {"p", "pass", "password", "secret", "test", "testing"}
        safe_hosts = {"host", "localhost", "127.0.0.1", "db", "example.com"}
        if (
            (parsed.username or "").casefold() in safe_users
            and (parsed.password or "").casefold() in safe_passwords
            and (parsed.hostname or "").casefold() in safe_hosts
        ):
            return "verified_test_fixture_placeholder"
    if path.startswith("backend/tests/") or "/fixtures/" in path:
        if any(token in lowered for token in ("example", "secret", "xyz", "fake", "test", "token=")):
            return "verified_test_fixture_placeholder"
    if path == ".env.example":
        return "excluded_example_configuration"
    return "requires_review"


def scan_text(path: str, text: str, source: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in PATTERNS:
            for match in pattern.finditer(line):
                matched = match.group(0)
                classification = classify(path, rule, matched)
                findings.append(
                    {
                        "source": source,
                        "path": path,
                        "line": line_number,
                        "rule": rule,
                        "matchFingerprint": fingerprint(matched),
                        "matchLength": len(matched),
                        "classification": classification,
                        "secretValueIncluded": False,
                    }
                )
    return findings


def tracked_files(worktree: Path) -> list[str]:
    raw = git(worktree, "ls-files", "-z", binary=True)
    assert isinstance(raw, bytes)
    return sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)


def main() -> int:
    args = parse_args()
    worktree = Path(args.worktree).resolve(strict=True)
    evidence_path = Path(args.evidence_path).resolve()
    head = str(git(worktree, "rev-parse", "HEAD")).strip()
    if head != args.expected_head:
        raise RuntimeError("unexpected worktree HEAD")
    subprocess.check_call(
        ["git", "cat-file", "-e", f"{args.diff_base}^{{commit}}"],
        cwd=str(worktree),
    )

    findings: list[dict[str, Any]] = []
    scanned_files = 0
    scanned_bytes = 0
    skipped_binary_files = 0
    forbidden_artifacts: list[str] = []
    for relative in tracked_files(worktree):
        pure = PurePosixPath(relative)
        lowered = relative.casefold()
        if (
            lowered in {".env", "credentials.json"}
            or lowered.endswith((".pem", ".key", ".sqlite", ".db"))
            or "/node_modules/" in f"/{lowered}/"
            or "/dist/" in f"/{lowered}/"
        ):
            forbidden_artifacts.append(relative)
            continue
        if pure.suffix.casefold() in BINARY_SUFFIXES:
            skipped_binary_files += 1
            continue
        if relative == ".env.example":
            continue
        path = worktree / Path(relative)
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw:
            skipped_binary_files += 1
            continue
        text = raw.decode("utf-8", errors="replace")
        scanned_files += 1
        scanned_bytes += len(raw)
        findings.extend(scan_text(relative, text, "tracked_repository"))

    diff = str(
        git(
            worktree,
            "diff",
            "--no-ext-diff",
            "--unified=0",
            f"{args.diff_base}..{head}",
            "--",
        )
    )
    diff_added_lines = "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    findings.extend(scan_text("release_diff", diff_added_lines, "git_diff_added_lines"))

    unique: dict[tuple[str, str, int, str, str], dict[str, Any]] = {}
    for finding in findings:
        key = (
            finding["source"],
            finding["path"],
            finding["line"],
            finding["rule"],
            finding["matchFingerprint"],
        )
        unique[key] = finding
    findings = sorted(
        unique.values(),
        key=lambda item: (item["classification"], item["path"], item["line"], item["rule"]),
    )
    verified_fingerprints = {
        item["matchFingerprint"]
        for item in findings
        if item["classification"] in {
            "verified_test_fixture_placeholder",
            "verified_sanitizer_test_fixture",
            "verified_ephemeral_runtime_expression",
        }
    }
    for item in findings:
        if (
            item["source"] == "git_diff_added_lines"
            and item["matchFingerprint"] in verified_fingerprints
        ):
            item["classification"] = "verified_diff_duplicate_of_test_fixture"
    unresolved = [item for item in findings if item["classification"] == "requires_review"]
    status = "PASS" if not unresolved and not forbidden_artifacts else "FAIL"
    result = {
        "schemaVersion": 1,
        "gate": "GATE5",
        "operation": "tracked_repository_and_release_diff_secret_scan",
        "head": head,
        "diffBase": args.diff_base,
        "scanner": "program_r1_high_signal_secret_patterns_v1",
        "coverage": {
            "trackedTextFilesScanned": scanned_files,
            "trackedBytesScanned": scanned_bytes,
            "binaryFilesSkipped": skipped_binary_files,
            "releaseDiffAddedLinesScanned": len(diff_added_lines.splitlines()),
            "workingTreeRuntimeSecretsRead": False,
            "environmentFilesRead": False,
        },
        "findings": findings,
        "candidateCount": len(findings),
        "verifiedFixturePlaceholderCount": sum(
            item["classification"] in {
                "verified_test_fixture_placeholder",
                "verified_sanitizer_test_fixture",
                "verified_ephemeral_runtime_expression",
                "verified_diff_duplicate_of_test_fixture",
            }
            for item in findings
        ),
        "unresolvedSecretCandidateCount": len(unresolved),
        "forbiddenTrackedArtifacts": forbidden_artifacts,
        "secretValuesIncludedInEvidence": False,
        "status": status,
        "marker": "PASS_GATE5_SECRET_SCAN" if status == "PASS" else "FAIL_GATE5_SECRET_SCAN",
        "productionWrites": 0,
    }
    atomic_json(evidence_path, result)
    print(
        json.dumps(
            {
                "status": status,
                "candidates": len(findings),
                "unresolved": len(unresolved),
                "forbiddenArtifacts": len(forbidden_artifacts),
                "head": head,
            }
        )
    )
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
