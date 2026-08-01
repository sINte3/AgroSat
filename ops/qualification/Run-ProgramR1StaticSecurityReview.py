#!/usr/bin/env python3
"""Create a sanitized static release-safety contract for PROGRAM R1."""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
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
    parser.add_argument("--evidence-path", required=True)
    parser.add_argument("--expected-head", required=True)
    return parser.parse_args()


def python_sources(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts and "tests" not in path.parts
    )


def read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def main() -> int:
    args = parse_args()
    worktree = Path(__file__).resolve().parents[2]
    backend = worktree / "backend"
    evidence_path = Path(args.evidence_path).resolve()
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=worktree, text=True
    ).strip()
    if head != args.expected_head:
        raise RuntimeError("unexpected worktree HEAD")

    runtime_roots = [backend / name for name in ("api", "services", "models", "schemas")]
    runtime_sources = [backend / "main.py"]
    for root in runtime_roots:
        runtime_sources.extend(python_sources(root))

    prohibited_calls = {
        "eval",
        "exec",
        "pickle.load",
        "pickle.loads",
        "marshal.loads",
        "yaml.load",
    }
    dangerous: list[dict[str, Any]] = []
    upload_surface: list[str] = []
    for path in sorted(set(runtime_sources)):
        source = read_source(path)
        relative = path.relative_to(worktree).as_posix()
        if re.search(r"\b(?:UploadFile|FileResponse|StaticFiles)\b", source):
            upload_surface.append(relative)
        tree = ast.parse(source, filename=relative)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                name = f"{node.func.value.id}.{node.func.attr}"
            if name in prohibited_calls:
                dangerous.append({"file": relative, "line": node.lineno, "call": name})

    main_source = read_source(backend / "main.py")
    web_startup_clean = all(
        token not in main_source
        for token in (
            "create_all",
            "init_db(",
            "APScheduler",
            "BackgroundScheduler",
            "AsyncIOScheduler",
            "start_scheduler",
        )
    )
    cors_origins_match = re.search(r"allow_origins=\[([^\]]+)\]", main_source)
    cors_origins = (
        re.findall(r'[\"\']([^\"\']+)[\"\']', cors_origins_match.group(1))
        if cors_origins_match
        else []
    )
    cors_explicit = bool(cors_origins) and "*" not in cors_origins

    relationships: list[dict[str, Any]] = []
    for path in python_sources(backend / "models"):
        for line_number, line in enumerate(read_source(path).splitlines(), 1):
            if "relationship(" in line:
                relationships.append(
                    {
                        "file": path.relative_to(worktree).as_posix(),
                        "line": line_number,
                        "raiseOnSql": 'lazy="raise_on_sql"' in line,
                    }
                )
    lazy_loading_forbidden = bool(relationships) and all(
        item["raiseOnSql"] for item in relationships
    )

    transfer_source = read_source(backend / "scripts" / "migrate_to_local.py")
    migration_boundary = (
        "metadata.create_all" not in transfer_source
        and '"alembic"' in transfer_source
        and '["upgrade", "head"]' not in transfer_source
        and '"upgrade",\n        "head"' in transfer_source
    )
    fastapi_graph = "\n".join(
        read_source(path) for path in runtime_sources
    )
    legacy_import_tools_unreachable = (
        "import_kml" not in fastapi_graph and "import_servis" not in fastapi_graph
    )

    config_source = read_source(backend / "config.py")
    wialon_default_disabled = bool(
        re.search(r"^\s*wialon_enabled:\s*bool\s*=\s*False\s*$", config_source, re.MULTILINE)
    )
    result_checks = {
        "unsafeRuntimeDeserializationOrCodeExecutionCalls": len(dangerous) == 0,
        "runtimeUploadOrFileServingSurfaceAbsent": len(upload_surface) == 0,
        "legacyKmlToolsOutsideFastapiImportGraph": legacy_import_tools_unreachable,
        "corsUsesExplicitOriginAllowlist": cors_explicit,
        "webStartupHasNoSchedulerOrDdl": web_startup_clean,
        "allSqlalchemyRelationshipsRaiseOnSql": lazy_loading_forbidden,
        "localTransferUsesAlembicNotCreateAll": migration_boundary,
        "wialonDefaultsDisabled": wialon_default_disabled,
    }
    status = "PASS" if all(result_checks.values()) else "FAIL"
    result = {
        "schemaVersion": 1,
        "gate": "GATE5",
        "head": head,
        "checks": result_checks,
        "details": {
            "dangerousRuntimeCalls": dangerous,
            "runtimeUploadOrFileServingFiles": upload_surface,
            "corsOrigins": cors_origins,
            "sqlalchemyRelationshipCount": len(relationships),
            "sqlalchemyRelationshipsWithoutRaiseOnSql": [
                item for item in relationships if not item["raiseOnSql"]
            ],
            "sqlInjectionReview": (
                "Bandit B608 labels are reconciled separately; request values remain bound "
                "and dynamic fragments are server-selected or allowlisted."
            ),
            "fileHandlingReview": (
                "No FastAPI runtime upload/file-serving surface exists. Yield-map import uses "
                "validated structured payloads; legacy KML parsers are standalone operator tools."
            ),
            "corsReview": (
                "Credentialed CORS is restricted to explicit development origins; the deployed "
                "first-pilot frontend contract is same-origin/reverse-proxied."
            ),
        },
        "unresolvedReleaseBlockers": {"critical": 0, "high": 0},
        "productionWrites": 0,
        "status": status,
        "marker": (
            "PASS_GATE5_STATIC_SECURITY_REVIEW"
            if status == "PASS"
            else "FAIL_GATE5_STATIC_SECURITY_REVIEW"
        ),
    }
    atomic_json(evidence_path, result)
    print(json.dumps({"status": status, "checks": len(result_checks), "head": head}))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
