"""Cross-process device ownership shared by the GUI and local executors."""

from __future__ import annotations

import ctypes
import hashlib
import os
import threading
from pathlib import Path


class DeviceBusyError(RuntimeError):
    """Raised when another process or thread owns the target device."""


_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x80
_WAIT_TIMEOUT = 0x102
_MUTEX_NAME_PREFIX = "MAA-LocalAI-Device-"


def device_mutex_name(address: str) -> str:
    normalized = address.strip().casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16].upper()
    return f"{_MUTEX_NAME_PREFIX}{digest}"


class DeviceControlLease:
    """Own one named mutex for the lifetime of a deterministic device run."""

    def __init__(
        self,
        address: str,
        *,
        timeout_seconds: float = 0.0,
    ):
        if not address.strip():
            raise ValueError("device address must not be empty")
        self.address = address.strip()
        self.timeout_seconds = max(0.0, timeout_seconds)
        self.name = device_mutex_name(self.address)
        self._handle: int | None = None
        self._owned = False
        self._thread_id: int | None = None

    def acquire(self) -> DeviceControlLease:
        if self._owned:
            if self._thread_id != threading.get_ident():
                raise DeviceBusyError(
                    f"device lease {self.name} is owned by another thread"
                )
            return self
        if os.name != "nt":
            raise DeviceBusyError(
                "cross-process device control is only implemented on Windows"
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_bool,
            ctypes.c_wchar_p,
        ]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel32.ReleaseMutex.restype = ctypes.c_bool
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool

        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            error = ctypes.get_last_error()
            raise DeviceBusyError(
                f"CreateMutex failed for {self.name}: WinError {error}"
            )

        timeout_ms = int(self.timeout_seconds * 1000)
        wait_result = kernel32.WaitForSingleObject(handle, timeout_ms)
        if wait_result not in {_WAIT_OBJECT_0, _WAIT_ABANDONED}:
            kernel32.CloseHandle(handle)
            if wait_result == _WAIT_TIMEOUT:
                raise DeviceBusyError(
                    f"device is busy: {self.address} ({self.name})"
                )
            raise DeviceBusyError(
                f"device mutex wait failed: {wait_result}"
            )

        self._handle = int(handle)
        self._owned = True
        self._thread_id = threading.get_ident()
        return self

    def release(self) -> None:
        if not self._owned:
            return
        if self._thread_id != threading.get_ident():
            raise DeviceBusyError(
                f"device lease {self.name} must be released by its owner thread"
            )
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel32.ReleaseMutex.restype = ctypes.c_bool
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        try:
            if not kernel32.ReleaseMutex(ctypes.c_void_p(self._handle)):
                error = ctypes.get_last_error()
                raise DeviceBusyError(
                    f"ReleaseMutex failed for {self.name}: WinError {error}"
                )
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(self._handle))
            self._handle = None
            self._owned = False
            self._thread_id = None

    def __enter__(self) -> DeviceControlLease:
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()


def device_lease_path(address: str, root: str | Path) -> Path:
    """Return the diagnostic lock-file path for a device."""

    return Path(root) / f"{device_mutex_name(address)}.lock"
