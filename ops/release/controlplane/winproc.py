"""Windows process and listener facts through documented Win32 APIs.

A process identity is its PID plus its kernel creation time, so a reused PID is
never mistaken for the process that was observed. Lineage is read from the
kernel's parent links, below an anchor the caller owns (a Task Scheduler
engine PID). Nothing here searches by process name.

``terminate_verified`` exists for one purpose only: the legacy supervisor
handoff, which ends the exact descendants captured below a pre-TASK_230
application task's engine process while that task was still running.
"""

from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass
import socket
import sys
from typing import Iterable


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    ppid: int
    name: str
    created: int  # FILETIME, 100 ns ticks since 1601 UTC; 0 when unreadable

    def evidence(self) -> dict:
        return asdict(self)


if sys.platform == "win32":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)

    class _ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    class _FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    _kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
    _kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(_FileTime),) * 4
    _kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _iphlpapi.GetExtendedTcpTable.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                                              wintypes.ULONG, ctypes.c_int, wintypes.ULONG)

_INVALID_HANDLE = ctypes.c_void_p(-1).value
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TCP_TABLE_OWNER_PID_LISTENER = 3


def _creation_time(pid: int) -> int:
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return 0
    try:
        created, exited, kernel, user = _FileTime(), _FileTime(), _FileTime(), _FileTime()
        if not _kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                         ctypes.byref(kernel), ctypes.byref(user)):
            return 0
        return (created.high << 32) | created.low
    finally:
        _kernel32.CloseHandle(handle)


def snapshot() -> dict[int, ProcessIdentity]:
    handle = _kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if handle in (None, 0, _INVALID_HANDLE):
        raise OSError("process snapshot unavailable")
    table: dict[int, ProcessIdentity] = {}
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = _kernel32.Process32FirstW(handle, ctypes.byref(entry))
        while more:
            pid = int(entry.th32ProcessID)
            table[pid] = ProcessIdentity(pid, int(entry.th32ParentProcessID), entry.szExeFile, _creation_time(pid))
            more = _kernel32.Process32NextW(handle, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(handle)
    return table


def lineage(anchor: int, table: dict[int, ProcessIdentity] | None = None) -> list[ProcessIdentity]:
    """The anchor and every descendant whose parent was created before it."""
    table = snapshot() if table is None else table
    if anchor not in table:
        return []
    found = [table[anchor]]
    frontier = [table[anchor]]
    while frontier:
        parent = frontier.pop()
        for identity in table.values():
            if identity.ppid == parent.pid and identity.pid != parent.pid and identity not in found:
                if parent.created and identity.created and identity.created < parent.created:
                    continue  # a reused parent PID: not a descendant
                found.append(identity)
                frontier.append(identity)
    return found


def alive(identities: Iterable[ProcessIdentity]) -> list[ProcessIdentity]:
    table = snapshot()
    return [identity for identity in identities
            if identity.pid in table and table[identity.pid].created == identity.created]


def listeners(ports: Iterable[int]) -> dict[int, int | None]:
    """The owning PID of each TCP listener on ``ports`` (IPv4 and IPv6), else None."""
    wanted = set(ports)
    found: dict[int, int | None] = {port: None for port in wanted}
    for family in (socket.AF_INET, socket.AF_INET6):
        size = wintypes.DWORD(0)
        _iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, family, _TCP_TABLE_OWNER_PID_LISTENER, 0)
        buffer = ctypes.create_string_buffer(size.value + 4096)
        size = wintypes.DWORD(len(buffer))
        if _iphlpapi.GetExtendedTcpTable(buffer, ctypes.byref(size), False, family,
                                         _TCP_TABLE_OWNER_PID_LISTENER, 0) != 0:
            raise OSError("TCP listener table unavailable")
        count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD))[0]
        # MIB_TCPROW_OWNER_PID is 6 DWORDs; MIB_TCP6ROW_OWNER_PID is 56 bytes.
        row_size, port_offset, pid_offset = (24, 8, 20) if family == socket.AF_INET else (56, 20, 52)
        for index in range(count):
            base = 4 + index * row_size
            raw_port = int.from_bytes(buffer.raw[base + port_offset:base + port_offset + 4], "little")
            port = socket.ntohs(raw_port & 0xFFFF)
            if port in wanted and found[port] is None:
                found[port] = int.from_bytes(buffer.raw[base + pid_offset:base + pid_offset + 4], "little")
    return found


def in_any_job(pid: int) -> bool | None:
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        result = wintypes.BOOL(False)
        return bool(result.value) if _kernel32.IsProcessInJob(handle, None, ctypes.byref(result)) else None
    finally:
        _kernel32.CloseHandle(handle)


def terminate_verified(identity: ProcessIdentity) -> bool:
    """End exactly this process instance; False if it is gone or its identity changed."""
    handle = _kernel32.OpenProcess(_PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION, False, identity.pid)
    if not handle:
        return False
    try:
        created, exited, kernel, user = _FileTime(), _FileTime(), _FileTime(), _FileTime()
        if not _kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                         ctypes.byref(kernel), ctypes.byref(user)):
            return False
        if ((created.high << 32) | created.low) != identity.created or not identity.created:
            return False
        return bool(_kernel32.TerminateProcess(handle, 1))
    finally:
        _kernel32.CloseHandle(handle)
