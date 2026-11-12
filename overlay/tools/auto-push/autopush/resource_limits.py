"""Conservative process-level resource limits for local automation."""

from __future__ import annotations

import ctypes
import os

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def lower_current_process_priority() -> int | None:
    """Lower the current Python process priority and return the previous flag."""

    if os.name != "nt":
        return None
    kernel32 = _kernel32()
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.GetPriorityClass.argtypes = [ctypes.c_void_p]
    kernel32.GetPriorityClass.restype = ctypes.c_uint32
    kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.SetPriorityClass.restype = ctypes.c_int
    handle = kernel32.GetCurrentProcess()
    previous = kernel32.GetPriorityClass(handle)
    if not previous:
        return None
    if not kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS):
        return None
    return int(previous)


def restore_current_process_priority(previous: int | None) -> None:
    if os.name != "nt" or previous is None:
        return
    kernel32 = _kernel32()
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.SetPriorityClass.restype = ctypes.c_int
    handle = kernel32.GetCurrentProcess()
    kernel32.SetPriorityClass(handle, previous)
