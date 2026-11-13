"""Minimal ctypes adapter for MAA Core Copilot execution."""

from __future__ import annotations

import ctypes
import json
import os
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable

from .device_control import DeviceControlLease
from .metrics import RoundMetrics
from .resource_limits import (
    lower_current_process_priority,
    restore_current_process_priority,
)
from .states import RunPhase

AsstBool = ctypes.c_uint8
AsstTaskId = ctypes.c_int32
AsstHandle = ctypes.c_void_p
AsstApiCallback = ctypes.CFUNCTYPE(
    None,
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_void_p,
)


class MaaCoreError(RuntimeError):
    """Raised when MAA Core rejects a call or task."""


class CoreMessage(IntEnum):
    INTERNAL_ERROR = 0
    INIT_FAILED = 1
    CONNECTION_INFO = 2
    ALL_TASKS_COMPLETED = 3
    ASYNC_CALL_INFO = 4
    DESTROYED = 5
    TASK_CHAIN_ERROR = 10_000
    TASK_CHAIN_START = 10_001
    TASK_CHAIN_COMPLETED = 10_002
    TASK_CHAIN_EXTRA_INFO = 10_003
    TASK_CHAIN_STOPPED = 10_004
    SUB_TASK_ERROR = 20_000
    SUB_TASK_START = 20_001
    SUB_TASK_COMPLETED = 20_002
    SUB_TASK_EXTRA_INFO = 20_003
    SUB_TASK_STOPPED = 20_004
    REPORT_REQUEST = 30_000


FATAL_CONNECTION_WHATS = {
    "ConnectFailed",
    "Disconnect",
    "ResolutionChanged",
    "ResolutionError",
    "ScreencapFailed",
    "TouchModeNotAvailable",
    "UnsupportedResolution",
}
SESSION_TERMINAL_MESSAGES = {
    CoreMessage.ALL_TASKS_COMPLETED,
    CoreMessage.TASK_CHAIN_ERROR,
    CoreMessage.TASK_CHAIN_STOPPED,
    CoreMessage.INTERNAL_ERROR,
    CoreMessage.INIT_FAILED,
    CoreMessage.DESTROYED,
}


@dataclass(frozen=True)
class MaaEvent:
    message_id: int
    details: dict[str, Any]

    @property
    def message_name(self) -> str:
        try:
            return CoreMessage(self.message_id).name
        except ValueError:
            value = self.details.get("what")
            return str(value) if isinstance(value, str) else "UNKNOWN"

    @property
    def task_id(self) -> int | None:
        value = self.details.get("taskid")
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value > 0 else None
        if isinstance(value, str) and value.isdigit():
            parsed = int(value)
            return parsed if parsed > 0 else None
        return None

    @property
    def task_chain(self) -> str | None:
        value = self.details.get("taskchain")
        return str(value) if value else None

    @property
    def fatal_connection(self) -> bool:
        return (
            self.message_id == CoreMessage.CONNECTION_INFO
            and self.details.get("what") in FATAL_CONNECTION_WHATS
        )


def _message_name(event: MaaEvent) -> str:
    return event.message_name


def summarize_events(
    events: list[MaaEvent],
    *,
    expected_task_id: int | None = None,
) -> dict[str, Any]:
    """Summarize callbacks without treating task completion as a clear result."""

    task_event_ids = {
        event.task_id
        for event in events
        if event.task_id is not None
        and _message_name(event)
        in {
            CoreMessage.TASK_CHAIN_START.name,
            CoreMessage.TASK_CHAIN_ERROR.name,
            CoreMessage.TASK_CHAIN_COMPLETED.name,
            CoreMessage.TASK_CHAIN_STOPPED.name,
        }
    }
    selected_task_id = expected_task_id
    if selected_task_id is None and task_event_ids:
        selected_task_id = next(
            event.task_id
            for event in reversed(events)
            if event.task_id in task_event_ids
        )

    def belongs_to_selected_task(event: MaaEvent) -> bool:
        return (
            selected_task_id is None
            or event.task_id is None
            or event.task_id == selected_task_id
        )

    relevant = [event for event in events if belongs_to_selected_task(event)]
    task_errors = [
        event.details
        for event in relevant
        if _message_name(event) == CoreMessage.TASK_CHAIN_ERROR.name
    ]
    connection_errors = [
        event.details
        for event in events
        if event.fatal_connection
    ]
    completed = any(
        _message_name(event) == CoreMessage.TASK_CHAIN_COMPLETED.name
        for event in relevant
    )
    stopped = any(
        _message_name(event) == CoreMessage.TASK_CHAIN_STOPPED.name
        for event in relevant
    )
    started = any(
        _message_name(event) == CoreMessage.TASK_CHAIN_START.name
        for event in relevant
    )
    actions = [
        event.details
        for event in relevant
        if event.details.get("what") == "CopilotAction"
    ]
    task_chain = next(
        (
            event.task_chain
            for event in reversed(relevant)
            if event.task_chain is not None
        ),
        None,
    )
    if connection_errors:
        terminal_state = "DISCONNECTED"
        phase = RunPhase.FAILED
    elif task_errors:
        terminal_state = "FAILED"
        phase = RunPhase.FAILED
    elif stopped:
        terminal_state = "STOPPED"
        phase = RunPhase.STOPPED
    elif completed:
        terminal_state = "COMPLETED"
        phase = RunPhase.VERIFYING
    elif started:
        terminal_state = "RUNNING"
        phase = RunPhase.RUNNING
    else:
        terminal_state = "UNKNOWN"
        phase = RunPhase.UNKNOWN
    return {
        "task_id": selected_task_id,
        "task_chain": task_chain,
        "started": started,
        "task_completed": terminal_state == "COMPLETED",
        "task_stopped": stopped,
        "terminal_state": terminal_state,
        "phase": phase.value,
        "task_errors": task_errors,
        "connection_errors": connection_errors,
        "copilot_action_count": len(actions),
        "event_count": len(events),
        "verification_required": True,
        "verified": False,
    }


def extract_oper_box_details(events: list[MaaEvent]) -> dict[str, Any] | None:
    """Return the final OperBox payload from nested MAA callbacks."""

    def find(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            own_opers = value.get("own_opers")
            if isinstance(own_opers, list) and value.get("done", True):
                return value
            for child in value.values():
                found = find(child)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find(child)
                if found is not None:
                    return found
        return None

    result = None
    for event in events:
        found = find(event.details)
        if found is not None:
            result = found
    return result


class MaaCoreLibrary:
    """Load MaaCore.dll and declare only the exports needed by auto-push."""

    def __init__(self, dll_path: str | Path):
        self.path = Path(dll_path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        if hasattr(os, "add_dll_directory"):
            self._dll_directory = os.add_dll_directory(str(self.path.parent))
        else:
            self._dll_directory = None
        self.library = ctypes.CDLL(str(self.path))
        self._declare()

    def _declare(self) -> None:
        library = self.library
        library.AsstGetVersion.argtypes = []
        library.AsstGetVersion.restype = ctypes.c_char_p

        library.AsstSetUserDir.argtypes = [ctypes.c_char_p]
        library.AsstSetUserDir.restype = AsstBool

        library.AsstLoadResource.argtypes = [ctypes.c_char_p]
        library.AsstLoadResource.restype = AsstBool

        library.AsstCreateEx.argtypes = [AsstApiCallback, ctypes.c_void_p]
        library.AsstCreateEx.restype = AsstHandle

        library.AsstDestroy.argtypes = [AsstHandle]
        library.AsstDestroy.restype = None

        library.AsstConnect.argtypes = [
            AsstHandle,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        ]
        library.AsstConnect.restype = AsstBool

        library.AsstAppendTask.argtypes = [
            AsstHandle,
            ctypes.c_char_p,
            ctypes.c_char_p,
        ]
        library.AsstAppendTask.restype = AsstTaskId

        library.AsstStart.argtypes = [AsstHandle]
        library.AsstStart.restype = AsstBool

        library.AsstStop.argtypes = [AsstHandle]
        library.AsstStop.restype = AsstBool

        library.AsstRunning.argtypes = [AsstHandle]
        library.AsstRunning.restype = AsstBool

        library.AsstConnected.argtypes = [AsstHandle]
        library.AsstConnected.restype = AsstBool

    def version(self) -> str:
        value = self.library.AsstGetVersion()
        return value.decode("utf-8", errors="replace") if value else ""


class MaaCoreSession:
    """Own one MAA Core instance and convert callbacks into local events."""

    def __init__(
        self,
        dll_path: str | Path,
        *,
        resource_dir: str | Path,
        user_dir: str | Path,
        metrics: RoundMetrics | None = None,
        event_sink: Callable[[MaaEvent], None] | None = None,
    ):
        self.metrics = metrics
        self._event_sink = event_sink
        self._events: list[MaaEvent] = []
        self._events_lock = threading.RLock()
        self._terminal_event = threading.Event()
        self._callback = AsstApiCallback(self._on_callback)
        self._event_sink_error: Exception | None = None
        self._library = MaaCoreLibrary(dll_path)

        if metrics is not None:
            metrics.record_tool_call(2)
        user = str(Path(user_dir).resolve()).encode("utf-8")
        resource_path = Path(resource_dir).resolve()
        # AsstLoadResource expects the directory that contains resource/, not
        # resource/ itself. Older callers passed the latter directly.
        resource_root = (
            resource_path.parent
            if resource_path.name.casefold() == "resource"
            else resource_path
        )
        resource = str(resource_root).encode("utf-8")
        if not self._library.library.AsstSetUserDir(user):
            raise MaaCoreError("AsstSetUserDir failed")
        if not self._library.library.AsstLoadResource(resource):
            raise MaaCoreError("AsstLoadResource failed")

        self.handle = self._library.library.AsstCreateEx(self._callback, None)
        if not self.handle:
            raise MaaCoreError("AsstCreateEx failed")

    @property
    def events(self) -> list[MaaEvent]:
        with self._events_lock:
            return list(self._events)

    def _on_callback(self, message_id: int, details: bytes | None, _: int) -> None:
        try:
            payload = json.loads(details.decode("utf-8")) if details else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {"raw": details.decode("utf-8", errors="replace")}
        event = MaaEvent(message_id=message_id, details=payload)
        with self._events_lock:
            self._events.append(event)
        if message_id in SESSION_TERMINAL_MESSAGES or event.fatal_connection:
            self._terminal_event.set()
        if self._event_sink is not None:
            try:
                self._event_sink(event)
            except Exception as exception:  # noqa: BLE001 - callback boundary
                self._event_sink_error = exception

    def connect(
        self,
        *,
        adb_path: str | Path,
        address: str,
        config: str,
    ) -> None:
        if self.metrics is not None:
            self.metrics.record_tool_call()
        result = self._library.library.AsstConnect(
            self.handle,
            str(adb_path).encode("utf-8"),
            address.encode("utf-8"),
            config.encode("utf-8"),
        )
        if not result:
            raise MaaCoreError("AsstConnect failed")

    def append_task(self, task_type: str, parameters: dict[str, Any]) -> int:
        if self.metrics is not None:
            self.metrics.record_tool_call()
        task_id = self._library.library.AsstAppendTask(
            self.handle,
            task_type.encode("utf-8"),
            json.dumps(parameters, ensure_ascii=False).encode("utf-8"),
        )
        if task_id <= 0:
            raise MaaCoreError(f"AsstAppendTask failed for {task_type}")
        return int(task_id)

    def start(self) -> None:
        if self.metrics is not None:
            self.metrics.record_tool_call()
        self._terminal_event.clear()
        if not self._library.library.AsstStart(self.handle):
            raise MaaCoreError("AsstStart failed")

    def stop(self) -> None:
        """Request a cooperative MaaCore stop without killing the game."""

        if not getattr(self, "handle", None):
            return
        if not self._library.library.AsstRunning(self.handle):
            return
        if self.metrics is not None:
            self.metrics.record_tool_call()
        if not self._library.library.AsstStop(self.handle):
            raise MaaCoreError("AsstStop failed")

    def wait(
        self,
        timeout_seconds: int = 600,
        *,
        stop_requested: Callable[[], bool] | None = None,
        stop_grace_seconds: float = 15.0,
    ) -> list[MaaEvent]:
        if self.metrics is not None:
            self.metrics.record_tool_call()
        deadline = time.monotonic() + timeout_seconds
        stop_deadline: float | None = None
        stop_sent = False
        while time.monotonic() < deadline:
            if (
                not stop_sent
                and stop_requested is not None
                and stop_requested()
            ):
                self.stop()
                stop_sent = True
                stop_deadline = time.monotonic() + stop_grace_seconds
            if self._terminal_event.wait(0.5):
                break
            if not self._library.library.AsstRunning(self.handle):
                break
            if stop_deadline is not None and time.monotonic() >= stop_deadline:
                break
        else:
            raise TimeoutError("MAA Core task did not finish before timeout")
        if self._event_sink_error is not None:
            raise MaaCoreError(
                f"event sink failed: {self._event_sink_error}"
            ) from self._event_sink_error
        return self.events

    def close(self) -> None:
        if getattr(self, "handle", None):
            if self.metrics is not None:
                self.metrics.record_tool_call()
            self._library.library.AsstDestroy(self.handle)
            self.handle = None

    def __enter__(self) -> MaaCoreSession:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def run_copilot_file(
    *,
    dll_path: str | Path,
    resource_dir: str | Path,
    user_dir: str | Path,
    adb_path: str | Path,
    address: str,
    connect_config: str,
    copilot_file: str | Path,
    metrics: RoundMetrics,
    timeout_seconds: int = 600,
    exercise: bool = False,
    formation: bool = False,
    formation_index: int = 0,
    add_trust: bool = False,
    ignore_requirements: bool = True,
    operbox_data_path: str | Path | None = None,
    support_unit_usage: int = 0,
    wait_until_end: bool = False,
    event_sink: Callable[[MaaEvent], None] | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> list[MaaEvent]:
    """Execute one local Copilot JSON and return all MAA callbacks."""

    metrics.consume("strategy_runs")
    task = build_copilot_task(
        copilot_file=copilot_file,
        exercise=exercise,
        formation=formation,
        formation_index=formation_index,
        add_trust=add_trust,
        ignore_requirements=ignore_requirements,
        operbox_data_path=operbox_data_path,
        support_unit_usage=support_unit_usage,
        wait_until_end=wait_until_end,
    )
    previous_priority = lower_current_process_priority()
    try:
        with DeviceControlLease(address):
            with MaaCoreSession(
                dll_path=dll_path,
                resource_dir=resource_dir,
                user_dir=user_dir,
                metrics=metrics,
                event_sink=event_sink,
            ) as session:
                session.connect(
                    adb_path=adb_path,
                    address=address,
                    config=connect_config,
                )
                session.append_task("Copilot", task)
                session.start()
                return session.wait(
                    timeout_seconds=timeout_seconds,
                    stop_requested=stop_requested,
                )
    finally:
        restore_current_process_priority(previous_priority)


def build_copilot_task(
    *,
    copilot_file: str | Path,
    exercise: bool = False,
    formation: bool = False,
    formation_index: int = 0,
    add_trust: bool = False,
    ignore_requirements: bool = True,
    operbox_data_path: str | Path | None = None,
    support_unit_usage: int = 0,
    wait_until_end: bool = False,
) -> dict[str, Any]:
    """Build one Copilot task without starting MaaCore."""

    task: dict[str, Any] = {
        "formation": formation,
        "formation_index": formation_index,
        "exercise": exercise,
        "support_unit_usage": support_unit_usage,
        "add_trust": add_trust,
        "ignore_requirements": ignore_requirements,
        "use_sanity_potion": False,
        "loop_times": 1,
    }
    canonical_path = str(Path(copilot_file).resolve())
    if wait_until_end:
        task["copilot_list"] = [
            {
                "id": 0,
                "filename": canonical_path,
            }
        ]
    else:
        task["filename"] = canonical_path
    if operbox_data_path is not None:
        task["operbox_data_path"] = str(Path(operbox_data_path).resolve())
    return task


def run_oper_box(
    *,
    dll_path: str | Path,
    resource_dir: str | Path,
    user_dir: str | Path,
    adb_path: str | Path,
    address: str,
    connect_config: str,
    metrics: RoundMetrics,
    timeout_seconds: int = 900,
) -> list[MaaEvent]:
    """Recognize the current roster through MAA's deterministic OperBox task."""

    previous_priority = lower_current_process_priority()
    try:
        with DeviceControlLease(address):
            with MaaCoreSession(
                dll_path=dll_path,
                resource_dir=resource_dir,
                user_dir=user_dir,
                metrics=metrics,
            ) as session:
                session.connect(
                    adb_path=adb_path,
                    address=address,
                    config=connect_config,
                )
                session.append_task("OperBox", {})
                session.start()
                return session.wait(timeout_seconds=timeout_seconds)
    finally:
        restore_current_process_priority(previous_priority)


def run_custom_tasks(
    *,
    dll_path: str | Path,
    resource_dir: str | Path,
    user_dir: str | Path,
    adb_path: str | Path,
    address: str,
    connect_config: str,
    task_names: list[str],
    metrics: RoundMetrics,
    timeout_seconds: int = 600,
) -> list[MaaEvent]:
    """Run one or more MAA custom task names through the Custom interface."""

    metrics.consume("strategy_runs")
    previous_priority = lower_current_process_priority()
    try:
        with DeviceControlLease(address):
            with MaaCoreSession(
                dll_path=dll_path,
                resource_dir=resource_dir,
                user_dir=user_dir,
                metrics=metrics,
            ) as session:
                session.connect(
                    adb_path=adb_path,
                    address=address,
                    config=connect_config,
                )
                session.append_task(
                    "Custom",
                    {"task_names": list(task_names)},
                )
                session.start()
                return session.wait(timeout_seconds=timeout_seconds)
    finally:
        restore_current_process_priority(previous_priority)
