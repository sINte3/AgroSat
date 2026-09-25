"""Process-tree ownership through a Windows Job Object.

A supervisor calls ``ProcessTreeOwnership.adopt_current_process()`` once,
before it starts any child. The call creates an anonymous Job Object limited
with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` and with every breakaway flag
cleared, and places the calling process inside it. From then on:

* every process the supervisor starts, and every process those start, is a
  member of the job. Breakaway is impossible: a child that asks for
  ``CREATE_BREAKAWAY_FROM_JOB`` is refused by the kernel, and a job nested
  below this one that allows silent breakaway (the Python venv launcher and
  libuv both create such jobs) only releases its members up to this job,
  which does not allow it.
* the only handle to the job lives in the supervisor. It is not inheritable
  and is never closed explicitly. When the supervisor ends for any reason,
  normal exit, crash, ``TerminateProcess`` from Task Scheduler, or the death
  of an enclosing job, the kernel closes the handle and terminates every
  remaining member.

Identity therefore comes from ownership: the kernel tracks membership. Nothing
here searches for processes by name, scans for orphans, or kills anything the
job does not contain.

The module imports on any platform so that its callers stay importable in
tests; ownership itself is only available on Windows.
"""

from __future__ import annotations

import ctypes
import os
import sys
from typing import Any

JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

_BASIC_ACCOUNTING_INFORMATION = 1
_BASIC_PROCESS_ID_LIST = 3
_EXTENDED_LIMIT_INFORMATION = 9
_MAX_LISTED_PROCESSES = 4096
_ERROR_MORE_DATA = 234


class OwnershipUnavailable(RuntimeError):
    """Process-tree ownership could not be established; nothing was started."""


if sys.platform == "win32":
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _BasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _ProcessIdList(ctypes.Structure):
        _fields_ = [
            ("NumberOfAssignedProcesses", wintypes.DWORD),
            ("NumberOfProcessIdsInList", wintypes.DWORD),
            ("ProcessIdList", ctypes.c_size_t * _MAX_LISTED_PROCESSES),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD))
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.GetCurrentProcess.argtypes = ()
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.IsProcessInJob.argtypes = (
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
    _kernel32.IsProcessInJob.restype = wintypes.BOOL


def _failure(code: str) -> OwnershipUnavailable:
    error = ctypes.get_last_error() if sys.platform == "win32" else 0
    return OwnershipUnavailable(f"{code}:winerror={error}")


class ProcessTreeOwnership:
    """The kill-on-close job that owns the calling supervisor's process tree.

    Create it with ``adopt_current_process()``. Keep the object alive for the
    life of the process; it deliberately has no ``close()``: closing the only
    handle terminates every member, the supervisor included.
    """

    def __init__(self, handle: int):
        self._handle = handle

    @classmethod
    def adopt_current_process(cls) -> "ProcessTreeOwnership":
        if sys.platform != "win32":
            raise OwnershipUnavailable("PROCESS_OWNERSHIP_REQUIRES_WINDOWS")
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise _failure("JOB_OBJECT_CREATE_FAILED")
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            handle, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            raise _failure("JOB_OBJECT_LIMIT_FAILED")
        ownership = cls(handle)
        if not _kernel32.AssignProcessToJobObject(handle, _kernel32.GetCurrentProcess()):
            raise _failure("JOB_OBJECT_ASSIGN_FAILED")
        if not ownership.contains_current_process():
            raise OwnershipUnavailable("JOB_OBJECT_MEMBERSHIP_UNVERIFIED")
        flags = ownership.limit_flags()
        if not flags & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE or flags & (
            JOB_OBJECT_LIMIT_BREAKAWAY_OK | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
        ):
            raise OwnershipUnavailable("JOB_OBJECT_LIMITS_UNVERIFIED")
        return ownership

    def contains_current_process(self) -> bool:
        result = wintypes.BOOL(False)
        if not _kernel32.IsProcessInJob(
            _kernel32.GetCurrentProcess(), self._handle, ctypes.byref(result)
        ):
            raise _failure("JOB_OBJECT_MEMBERSHIP_QUERY_FAILED")
        return bool(result.value)

    def limit_flags(self) -> int:
        limits = _ExtendedLimitInformation()
        if not _kernel32.QueryInformationJobObject(
            self._handle, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits),
            ctypes.sizeof(limits), None,
        ):
            raise _failure("JOB_OBJECT_LIMIT_QUERY_FAILED")
        return int(limits.BasicLimitInformation.LimitFlags)

    def member_process_ids(self) -> list[int]:
        """Every live member, the supervisor included, as the kernel lists it."""
        listing = _ProcessIdList()
        if not _kernel32.QueryInformationJobObject(
            self._handle, _BASIC_PROCESS_ID_LIST, ctypes.byref(listing),
            ctypes.sizeof(listing), None,
        ) and ctypes.get_last_error() != _ERROR_MORE_DATA:
            raise _failure("JOB_OBJECT_MEMBER_QUERY_FAILED")
        count = min(int(listing.NumberOfProcessIdsInList), _MAX_LISTED_PROCESSES)
        return sorted(int(listing.ProcessIdList[index]) for index in range(count))

    def accounting(self) -> dict[str, int]:
        info = _BasicAccountingInformation()
        if not _kernel32.QueryInformationJobObject(
            self._handle, _BASIC_ACCOUNTING_INFORMATION, ctypes.byref(info),
            ctypes.sizeof(info), None,
        ):
            raise _failure("JOB_OBJECT_ACCOUNTING_QUERY_FAILED")
        return {
            "total_processes": int(info.TotalProcesses),
            "active_processes": int(info.ActiveProcesses),
            "terminated_processes": int(info.TotalTerminatedProcesses),
        }

    def evidence(self) -> dict[str, Any]:
        """Machine-readable proof of the ownership contract, without secrets."""
        flags = self.limit_flags()
        return {
            "mechanism": "windows_job_object",
            "owner_pid": os.getpid(),
            "owner_in_job": self.contains_current_process(),
            "kill_on_job_close": bool(flags & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE),
            "breakaway_allowed": bool(flags & JOB_OBJECT_LIMIT_BREAKAWAY_OK),
            "silent_breakaway_allowed": bool(flags & JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK),
            "member_pids": self.member_process_ids(),
            **self.accounting(),
        }
