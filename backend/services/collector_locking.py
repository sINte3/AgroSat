"""
Cross-process mutex-based locking for satellite collection CLI.

Uses Win32 CreateMutexW via ctypes for reliable cross-process mutual exclusion.
No pywin32 dependency required.

Lock file tracks PID for identification and stale-lock detection.

Usage:
    lock_path = acquire_lock()
    try:
        # do work
    finally:
        release_lock(lock_path)
"""

import atexit
import ctypes
import logging
import os
import sys
from ctypes import wintypes

logger = logging.getLogger(__name__)

DEFAULT_LOCK_DIR = os.environ.get("AGROSAT_LOCK_DIR") or (
    os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp"
)
DEFAULT_LOCK_FILE = os.path.join(DEFAULT_LOCK_DIR, "agrosat_satellite_collector.lock")

MUTEX_NAME = "Global\\AgroSatSatelliteCollector_v1"

_held_lock_path: str | None = None
_held_mutex_handle: int | None = None

# Win32 constants
MUTEX_MODIFY_STATE = 0x0001
SYNCHRONIZE = 0x00100000
MUTEX_ALL_ACCESS = 0x001F0001
WAIT_TIMEOUT = 0x00000102
WAIT_OBJECT_0 = 0x00000000
INFINITE = 0xFFFFFFFF


@atexit.register
def _atexit_cleanup():
    """Release lock on process exit (normal or exception)."""
    if _held_lock_path:
        release_lock(_held_lock_path)


def _create_mutex(mutex_name: str = MUTEX_NAME) -> int:
    """Create or open a named mutex using Win32 API. Returns handle."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    # SECURITY_ATTRIBUTES with NULL (default security)
    handle = kernel32.CreateMutexW(None, False, mutex_name)
    if not handle:
        err = ctypes.get_last_error()
        raise RuntimeError(f"CreateMutexW failed: error {err}")
    return handle


def _wait_mutex(handle: int, timeout_ms: int = 0) -> int:
    """Try to acquire mutex. Returns WAIT_OBJECT_0 on success."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    return kernel32.WaitForSingleObject(handle, timeout_ms)


def _release_mutex(handle: int) -> bool:
    """Release mutex."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    return kernel32.ReleaseMutex(handle)


def _close_handle(handle: int) -> bool:
    """Close a Win32 handle."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32.CloseHandle(handle)


def _read_lock_content(path: str) -> tuple[int | None, str | None]:
    """Read lock file. Returns (PID, raw_content) or (None, error/reason)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return None, None
        pid_str = raw.split(",")[0].strip().replace("PID=", "")
        try:
            return int(pid_str), raw
        except ValueError:
            return None, raw
    except FileNotFoundError:
        return None, None
    except Exception as e:
        return None, str(e)


def _write_pid(path: str) -> None:
    """Write current PID to lock file."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"PID={os.getpid()},agrosat-collector\n")
        f.flush()
        os.fsync(f.fileno())


def _pid_is_alive(pid: int) -> bool:
    """Check whether a PID exists on Windows."""
    try:
        handle = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        return True  # assume alive if check fails


def acquire_lock(
    lock_file: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    mutex_name: str = MUTEX_NAME,
) -> str | None:
    """
    Acquire cross-process lock via Win32 named mutex.

    Args:
        lock_file: Path to lock file. Defaults to DEFAULT_LOCK_FILE.
        force: Break existing lock and re-acquire.
        dry_run: Check only, don't acquire.

    Returns:
        Lock file path if acquired (not dry-run), None if dry-run.

    Raises:
        SystemExit (exit 3): Lock held by another process.
    """
    global _held_lock_path, _held_mutex_handle

    lock_path = lock_file or DEFAULT_LOCK_FILE
    lock_dir = os.path.dirname(lock_path)

    try:
        os.makedirs(lock_dir, exist_ok=True)
    except Exception as e:
        logger.warning("Could not create lock dir %s: %s", lock_dir, e)

    existing_pid, raw = _read_lock_content(lock_path)

    if dry_run:
        if existing_pid is not None and _pid_is_alive(existing_pid):
            print(f"  LOCK STATUS: Held by PID={existing_pid} (dry-run)")
        elif existing_pid is not None:
            print(f"  LOCK STATUS: Stale PID={existing_pid} (dry-run)")
        else:
            print(f"  LOCK STATUS: Available (dry-run)")
        return None

    try:
        mutex_handle = _create_mutex(mutex_name)
    except RuntimeError as e:
        print(f"  LOCK ERROR: {e}", file=sys.stderr)
        sys.exit(3)

    wait_result = _wait_mutex(mutex_handle, timeout_ms=0)

    if wait_result == WAIT_TIMEOUT:
        # Mutex held by another process
        _close_handle(mutex_handle)

        if force:
            # Force: open existing mutex, release it, re-create
            print(f"  LOCK: Force-override previous lock")
            try:
                force_handle = _create_mutex(mutex_name)
                _wait_mutex(force_handle, timeout_ms=5000)
                if _held_mutex_handle:
                    _close_handle(_held_mutex_handle)
                _held_mutex_handle = force_handle
            except RuntimeError as e:
                print(f"  LOCK ERROR: Force acquire failed: {e}", file=sys.stderr)
                sys.exit(3)
        else:
            if existing_pid is not None and not _pid_is_alive(existing_pid):
                print(
                    f"  LOCK ERROR: Stale lock from PID={existing_pid}. "
                    f"Use --force-lock to override.",
                    file=sys.stderr,
                )
                print(f"  LOCK PATH: {lock_path}", file=sys.stderr)
                sys.exit(3)
            else:
                print(
                    f"  LOCK ERROR: Collector already running "
                    f"(PID={existing_pid or 'unknown'}). Lock: {lock_path}",
                    file=sys.stderr,
                )
                print(f"  Use --force-lock to override.", file=sys.stderr)
                sys.exit(3)
    elif wait_result == WAIT_OBJECT_0:
        # Acquired
        _held_mutex_handle = mutex_handle
    else:
        _close_handle(mutex_handle)
        print(f"  LOCK ERROR: Unexpected wait result {wait_result}", file=sys.stderr)
        sys.exit(3)

    _write_pid(lock_path)
    _held_lock_path = lock_path
    print(f"  LOCK: Acquired (PID={os.getpid()})")
    return lock_path


def release_lock(lock_file: str | None = None) -> None:
    """Release lock. Idempotent."""
    global _held_lock_path, _held_mutex_handle

    lock_path = lock_file or _held_lock_path

    if _held_mutex_handle:
        try:
            _release_mutex(_held_mutex_handle)
            _close_handle(_held_mutex_handle)
        except Exception:
            pass
        _held_mutex_handle = None

    if lock_path and os.path.exists(lock_path):
        try:
            os.unlink(lock_path)
            print(f"  LOCK: Released ({lock_path})")
        except OSError:
            pass

    _held_lock_path = None
