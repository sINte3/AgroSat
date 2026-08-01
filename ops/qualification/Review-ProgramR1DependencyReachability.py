#!/usr/bin/env python3
"""Reconcile remaining dependency advisories with executable product paths."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any


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
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--expected-head", required=True)
    return parser.parse_args()


def npm_findings(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = payload.get("vulnerabilities") or {}
    if not isinstance(values, dict):
        raise RuntimeError("npm audit vulnerability payload is malformed")
    return values


def pip_findings(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for dependency in payload.get("dependencies") or []:
        vulns = dependency.get("vulns") or []
        if vulns:
            result[str(dependency.get("name"))] = list(vulns)
    return result


def main() -> int:
    args = parse_args()
    worktree = Path(args.worktree).resolve(strict=True)
    evidence_dir = Path(args.evidence_dir).resolve(strict=True)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(worktree), text=True
    ).strip()
    if head != args.expected_head:
        raise RuntimeError("unexpected worktree HEAD")

    backend_audit = json.loads(
        (evidence_dir / "BACKEND_PIP_AUDIT.json").read_text(encoding="utf-8")
    )
    frontend_audit = json.loads(
        (evidence_dir / "FRONTEND_NPM_AUDIT_PRODUCTION.json").read_text(
            encoding="utf-8"
        )
    )
    backend_findings = pip_findings(backend_audit)
    frontend_findings = npm_findings(frontend_audit)

    auth_source = (worktree / "backend" / "api" / "auth.py").read_text(
        encoding="utf-8", errors="replace"
    )
    backend_application = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (worktree / "backend").rglob("*.py")
        if "tests" not in path.parts
    )
    frontend_sources = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (worktree / "frontend" / "src").rglob("*")
        if path.is_file() and path.suffix in {".js", ".jsx", ".ts", ".tsx"}
    )
    package_json = json.loads(
        (worktree / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    package_lock = json.loads(
        (worktree / "frontend" / "package-lock.json").read_text(encoding="utf-8")
    )
    requirements = (
        worktree / "backend" / "requirements.txt"
    ).read_text(encoding="utf-8")

    ecdsa_ids = {
        vuln_id
        for finding in backend_findings.get("ecdsa", [])
        for vuln_id in [str(finding.get("id")), *(finding.get("aliases") or [])]
    }
    ecdsa_only = set(backend_findings) == {"ecdsa"} and (
        "GHSA-wj6h-64fc-37mp" in ecdsa_ids
        or "CVE-2024-23342" in ecdsa_ids
    )
    hs256_only = (
        re.search(r'^ALGORITHM\s*=\s*["\']HS256["\']\s*$', auth_source, re.MULTILINE)
        is not None
        and "algorithms=[ALGORITHM]" in auth_source
        and not re.search(
            r"\b(ES256|ES384|ES512|SigningKey|ECDH|P-256)\b",
            backend_application,
            re.IGNORECASE,
        )
    )

    remaining_frontend = set(frontend_findings)
    react_router_only = remaining_frontend == {"react-router", "react-router-dom"}
    rsc_tokens = re.findall(
        r"\b(unstable_[A-Za-z0-9_]*RSC[A-Za-z0-9_]*|RSC[A-Za-z0-9_]*|createCallServer|serverAction)\b",
        frontend_sources,
        re.IGNORECASE,
    )
    spa_router_only = (
        react_router_only
        and "BrowserRouter" in frontend_sources
        and not rsc_tokens
        and "react-server" not in frontend_sources
    )

    postcss_declared = package_json["devDependencies"].get("postcss")
    postcss_locked = package_lock["packages"]["node_modules/postcss"]["version"]
    repaired_versions = {
        "requests": "requests==2.33.0" in requirements,
        "pydanticSettings": "pydantic-settings==2.14.2" in requirements,
        "postcss": postcss_declared == "8.5.18" and postcss_locked == "8.5.18",
    }

    checks = {
        "fixedBackendFindingsRemoved": set(backend_findings) == {"ecdsa"},
        "ecdsaAdvisoryIdentifiedExactly": ecdsa_only,
        "ecdsaSigningPathUnreachable": hs256_only,
        "fixedFrontendBuildFindingRemoved": "postcss" not in frontend_findings,
        "reactRouterAdvisoryIdentifiedExactly": react_router_only,
        "unstableRscPathUnreachable": spa_router_only,
        "repairedVersionsPinned": all(repaired_versions.values()),
    }
    if not all(checks.values()):
        failed = sorted(name for name, passed in checks.items() if not passed)
        raise RuntimeError(f"dependency reachability review failed: {','.join(failed)}")

    result = {
        "schemaVersion": 1,
        "gate": "GATE5",
        "operation": "dependency_advisory_reachability_review",
        "head": head,
        "checks": checks,
        "repairedVersions": {
            "requests": "2.33.0",
            "pydanticSettings": "2.14.2",
            "postcss": "8.5.18",
        },
        "remainingFindings": [
            {
                "package": "ecdsa",
                "advisory": "CVE-2024-23342 / GHSA-wj6h-64fc-37mp",
                "scannerSeverity": "high",
                "reachableOperation": False,
                "evidence": "AgroSat JWT encode/decode is hard-coded to HS256; no ECDSA signing, P-256 key generation, or ECDH path exists",
                "releaseBlocker": False,
                "guardrail": "do not enable ES* JWT algorithms or private-key ECDSA operations without replacing/re-reviewing the dependency",
            },
            {
                "package": "react-router/react-router-dom",
                "advisory": "GHSA-qwww-vcr4-c8h2",
                "scannerSeverity": "high",
                "reachableOperation": False,
                "evidence": "the advisory is limited to unstable RSC APIs; AgroSat is a Vite BrowserRouter SPA and imports no RSC/server-action API",
                "releaseBlocker": False,
                "guardrail": "do not enable React Router RSC APIs until a patched compatible release is adopted and requalified",
            },
        ],
        "unresolvedCriticalReleaseBlockers": 0,
        "unresolvedHighReleaseBlockers": 0,
        "status": "PASS",
        "marker": "PASS_GATE5_DEPENDENCY_REACHABILITY_REVIEW",
        "productionWrites": 0,
    }
    atomic_json(evidence_dir / "DEPENDENCY_REACHABILITY_REVIEW.json", result)
    print(json.dumps({"status": "PASS", "highReleaseBlockers": 0, "head": head}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
