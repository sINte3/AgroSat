"""Read-only Git identity and immutable release material.

Only these Git operations exist here: reading objects and refs, ``fetch`` of
remote-tracking refs, ``archive`` of one exact commit, and a verification that
uses a temporary index file outside the repository's own index. Nothing
checks out, resets, rebases, commits, tags or pushes.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

from .common import ControlPlaneError, require_sha, sha256_file

EXCLUDED_SEGMENTS = frozenset({"node_modules", "venv", ".venv", ".pytest_cache", "htmlcov", "__pycache__"})
SAFE_REF_PREFIX = "refs/remotes/"


class Git:
    def __init__(self, repository: Path, *, executable: str = "git"):
        self.repository = Path(repository)
        self.executable = executable

    def run(self, *arguments: str, env: dict[str, str] | None = None, check: bool = True,
            timeout: int = 600) -> subprocess.CompletedProcess:
        result = subprocess.run([self.executable, "-C", str(self.repository), *arguments],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                env=env, timeout=timeout)
        if check and result.returncode != 0:
            raise ControlPlaneError("GIT_COMMAND_FAILED", f"git {arguments[0]} exited {result.returncode}")
        return result

    def version(self) -> str:
        return self.run("--version").stdout.strip()

    def config(self, key: str) -> str | None:
        result = self.run("config", "--get", key, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def fetch(self, remote: str = "origin") -> None:
        self.run("fetch", "--prune", "--no-tags", remote, timeout=900)

    def commit(self, sha: str) -> str:
        require_sha(sha, "GIT_COMMIT_IDENTITY_REJECTED")
        result = self.run("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}", check=False)
        if result.returncode != 0 or result.stdout.strip() != sha:
            raise ControlPlaneError("GIT_COMMIT_MISSING", sha)
        return sha

    def ref(self, name: str) -> str | None:
        result = self.run("rev-parse", "--verify", "--quiet", f"{name}^{{commit}}", check=False)
        value = result.stdout.strip()
        return value if result.returncode == 0 and value else None

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        result = self.run("merge-base", "--is-ancestor", ancestor, descendant, check=False)
        if result.returncode not in (0, 1):
            raise ControlPlaneError("GIT_ANCESTRY_UNRESOLVED")
        return result.returncode == 0

    def commit_count(self, base: str, head: str) -> int:
        return int(self.run("rev-list", "--count", f"{base}..{head}").stdout.strip())

    def remote_refs_containing(self, sha: str, remote: str = "origin") -> list[str]:
        output = self.run("for-each-ref", "--contains", sha, "--format=%(refname)",
                          f"{SAFE_REF_PREFIX}{remote}/").stdout
        return sorted(line.strip()[len(SAFE_REF_PREFIX):] for line in output.splitlines()
                      if line.strip() and not line.strip().endswith("/HEAD"))

    def tree(self, sha: str, path: str = "") -> str | None:
        result = self.run("rev-parse", "--verify", "--quiet", f"{sha}:{path}", check=False)
        value = result.stdout.strip()
        return value if result.returncode == 0 and value else None

    def tracked_files(self, sha: str) -> list[str]:
        return [line for line in self.run("ls-tree", "-r", "--name-only", sha).stdout.splitlines() if line]

    def changed_files(self, base: str, head: str) -> list[str]:
        return [line for line in self.run("diff", "--name-only", f"{base}..{head}").stdout.splitlines() if line]

    def archive(self, sha: str, destination: Path) -> dict:
        """``git archive --format=zip`` of exactly ``sha``; never overwrites."""
        self.commit(sha)
        destination = Path(destination)
        if destination.exists():
            raise ControlPlaneError("SOURCE_ARCHIVE_ALREADY_EXISTS", destination.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.run("archive", "--format=zip", f"--output={destination}", sha)
        with zipfile.ZipFile(destination) as package:
            if package.comment.decode("ascii", errors="replace").strip() != sha:
                raise ControlPlaneError("SOURCE_ARCHIVE_IDENTITY_REJECTED")
        return {"format": "zip", "sha256": sha256_file(destination), "bytes": destination.stat().st_size,
                "git_version": self.version(), "core_autocrlf": self.config("core.autocrlf")}

    def verify_worktree_material(self, sha: str, material: Path) -> dict:
        """Every tracked file of ``sha`` is present in ``material`` with identical content.

        Uses a temporary index (GIT_INDEX_FILE) so the repository's own index is
        never touched; Git applies its own line-ending rules when comparing.
        Extra untracked files in ``material`` (venv, dist, manifest) are allowed.
        """
        self.commit(sha)
        with tempfile.TemporaryDirectory(prefix="agrosat-verify-") as directory:
            environment = dict(os.environ, GIT_INDEX_FILE=str(Path(directory) / "index"))
            self.run("read-tree", sha, env=environment)
            work_tree = f"--work-tree={material}"
            self.run(work_tree, "update-index", "-q", "--really-refresh", env=environment, check=False)
            differing = [line for line in self.run(work_tree, "diff-files", "--name-only",
                                                   env=environment).stdout.splitlines() if line]
        if differing:
            raise ControlPlaneError("RELEASE_MATERIAL_MISMATCH", f"{len(differing)} tracked files differ",
                                    files=differing[:20])
        return {"tracked_files_verified": len(self.tracked_files(sha)), "differing": []}


def excluded_tracked_content(files: list[str]) -> list[str]:
    """Tracked paths that must never ship in release material (secrets, environments, caches)."""
    bad = []
    for name in files:
        segments = name.replace("\\", "/").split("/")
        if any(segment in EXCLUDED_SEGMENTS for segment in segments[:-1]) or any(
            segment == ".env" or (segment.startswith(".env.") and segment != ".env.example")
            for segment in segments
        ):
            bad.append(name)
    return sorted(bad)


def extract_archive(archive: Path, destination: Path) -> int:
    """Extract a Git zip archive into a new directory, refusing unsafe entries."""
    destination = Path(destination)
    if destination.exists():
        raise ControlPlaneError("IMMUTABLE_RELEASE_ALREADY_EXISTS", destination.name)
    root = destination.resolve()
    count = 0
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        if len(set(names)) != len(names):
            raise ControlPlaneError("SOURCE_ARCHIVE_DUPLICATE_PATH")
        for name in names:
            segments = name.rstrip("/").replace("\\", "/").split("/")
            if not name or name.startswith(("/", "\\")) or ":" in name or any(
                segment in ("", ".", "..") or segment.endswith((" ", ".")) for segment in segments
            ):
                raise ControlPlaneError("SOURCE_ARCHIVE_UNSAFE_PATH", name)
            target = (root / name).resolve()
            if target != root and root not in target.parents:
                raise ControlPlaneError("SOURCE_ARCHIVE_EXTRACTION_ESCAPE", name)
        destination.mkdir(parents=True)
        for info in package.infolist():
            target = root / info.filename
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(info) as source, target.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
            count += 1
    return count
