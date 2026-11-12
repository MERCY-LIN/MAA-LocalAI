"""Persistent run evidence for the LocalAI debugging CLI."""

from __future__ import annotations

import ctypes
import json
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .states import RunPhase, phase_from_status


SCHEMA_VERSION = "localai.debug/v1"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
ACTIVE_RUN_FILE = "active-run.json"
MANIFEST_FILE = "manifest.json"
STATUS_FILE = "status.json"
EVENTS_FILE = "events.jsonl"
STOP_REQUEST_FILE = "stop.request.json"


class EvidenceError(RuntimeError):
    """Raised when run evidence cannot be created or read safely."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def new_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"run-{stamp}-{uuid.uuid4().hex[:8]}"


def new_attempt_id() -> str:
    return f"attempt-{uuid.uuid4().hex[:12]}"


def validate_identifier(value: str, label: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(value):
        raise EvidenceError(f"invalid {label}: {value!r}")
    return value


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            ctypes.c_uint32,
            ctypes.c_bool,
            ctypes.c_uint32,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            error = ctypes.get_last_error()
            return error == 5
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exception:
        raise EvidenceError(f"cannot read JSON evidence {path}: {exception}") from exception
    if not isinstance(value, dict):
        raise EvidenceError(f"JSON evidence is not an object: {path}")
    return value


def result_envelope(
    command: str,
    status: RunPhase | str,
    *,
    run_id: str | None = None,
    attempt_id: str | None = None,
    data: dict[str, Any] | None = None,
    errors: list[str] | None = None,
    artifacts: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "status": phase_from_status(status).value
        if isinstance(status, RunPhase)
        else status,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "timestamp": utc_now(),
        "data": data or {},
        "errors": errors or [],
        "artifacts": artifacts or [],
    }


class RunStore:
    """Own one run directory and make status/event updates recoverable."""

    def __init__(
        self,
        evidence_root: str | Path,
        *,
        run_id: str | None = None,
        attempt_id: str | None = None,
    ):
        self.evidence_root = Path(evidence_root).resolve()
        self.run_id = validate_identifier(run_id or new_run_id(), "run_id")
        self.attempt_id = validate_identifier(
            attempt_id or new_attempt_id(),
            "attempt_id",
        )
        self.run_dir = self.evidence_root / self.run_id
        self._event_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self.run_dir.mkdir(parents=True, exist_ok=True)

    @property
    def status_path(self) -> Path:
        return self.run_dir / STATUS_FILE

    @property
    def manifest_path(self) -> Path:
        return self.run_dir / MANIFEST_FILE

    @property
    def events_path(self) -> Path:
        return self.run_dir / EVENTS_FILE

    @property
    def stop_request_path(self) -> Path:
        return self.run_dir / STOP_REQUEST_FILE

    @property
    def active_run_path(self) -> Path:
        return self.evidence_root / ACTIVE_RUN_FILE

    def create_manifest(self, payload: dict[str, Any]) -> dict[str, Any]:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "created_at": utc_now(),
            **payload,
        }
        write_json_atomic(self.manifest_path, manifest)
        return manifest

    def update_status(
        self,
        status: RunPhase | str,
        **fields: Any,
    ) -> dict[str, Any]:
        with self._state_lock:
            current = read_json(self.status_path) or {}
            payload = {
                **current,
                **fields,
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "attempt_id": self.attempt_id,
                "status": (
                    status.value if isinstance(status, RunPhase) else status
                ),
                "updated_at": utc_now(),
            }
            if current.get("started_at") is None:
                payload["started_at"] = payload["updated_at"]
            write_json_atomic(self.status_path, payload)
            return payload

    def mark_active(self) -> None:
        lock_path = self.active_run_path.with_name("active-run.lock.json")
        descriptor: int | None = None
        for _ in range(2):
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                break
            except FileExistsError as exception:
                lock = read_json(lock_path) or {}
                lock_pid = lock.get("pid")
                if isinstance(lock_pid, int) and not process_is_running(lock_pid):
                    lock_path.unlink(missing_ok=True)
                    continue
                raise EvidenceError(
                    f"another debug run is active: {lock_path}"
                ) from exception
        if descriptor is None:
            raise EvidenceError(f"unable to acquire active run lock: {lock_path}")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "run_id": self.run_id,
                        "attempt_id": self.attempt_id,
                        "pid": os.getpid(),
                    },
                    handle,
                )
                handle.flush()
                os.fsync(handle.fileno())
            write_json_atomic(
                self.active_run_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": self.run_id,
                    "attempt_id": self.attempt_id,
                    "pid": os.getpid(),
                    "updated_at": utc_now(),
                },
            )
        except Exception:
            lock_path.unlink(missing_ok=True)
            raise

    def clear_active(self) -> None:
        active = read_json(self.active_run_path)
        if active and active.get("run_id") == self.run_id:
            self.active_run_path.unlink(missing_ok=True)
        lock_path = self.active_run_path.with_name("active-run.lock.json")
        lock = read_json(lock_path)
        if lock and lock.get("run_id") == self.run_id:
            lock_path.unlink(missing_ok=True)

    def append_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        event = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "event_id": uuid.uuid4().hex,
            "timestamp": utc_now(),
            "type": event_type,
            "data": payload,
        }
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with self._event_lock:
            with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return event

    def read_events(
        self,
        *,
        after: int = 0,
        limit: int = 1000,
    ) -> tuple[list[dict[str, Any]], int]:
        if after < 0:
            raise EvidenceError("after must be non-negative")
        if limit < 1:
            raise EvidenceError("limit must be positive")
        if not self.events_path.is_file():
            return [], 0
        events: list[dict[str, Any]] = []
        line_number = 0
        with self.events_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line_number += 1
                if line_number <= after:
                    continue
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exception:
                    raise EvidenceError(
                        f"invalid event JSON at {self.events_path}:{line_number}"
                    ) from exception
                if not isinstance(event, dict):
                    raise EvidenceError(
                        f"event is not an object at {self.events_path}:{line_number}"
                    )
                events.append(event)
                if len(events) >= limit:
                    break
        return events, after + len(events)

    def request_stop(self, *, reason: str, requested_by: str) -> dict[str, Any]:
        request = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "requested_at": utc_now(),
            "requested_by": requested_by,
            "reason": reason,
        }
        write_json_atomic(self.stop_request_path, request)
        self.append_event("stop_requested", request)
        return request

    def stop_requested(self) -> bool:
        return self.stop_request_path.is_file()

    def read_stop_request(self) -> dict[str, Any] | None:
        return read_json(self.stop_request_path)


def find_run_store(
    evidence_root: str | Path,
    *,
    run_id: str | None = None,
) -> RunStore:
    root = Path(evidence_root).resolve()
    if run_id is not None:
        validate_identifier(run_id, "run_id")
        run_dir = root / run_id
        if not run_dir.is_dir():
            raise EvidenceError(f"run not found: {run_id}")
        status = read_json(run_dir / STATUS_FILE) or {}
        attempt_id = status.get("attempt_id")
        return RunStore(
            root,
            run_id=run_id,
            attempt_id=str(attempt_id) if attempt_id else new_attempt_id(),
        )

    active = read_json(root / ACTIVE_RUN_FILE)
    if active and active.get("run_id"):
        return find_run_store(root, run_id=str(active["run_id"]))

    candidates = sorted(
        (path for path in root.glob("run-*") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise EvidenceError("no run evidence exists")
    return find_run_store(root, run_id=candidates[0].name)


def ensure_no_active_run(evidence_root: str | Path) -> None:
    root = Path(evidence_root).resolve()
    active = read_json(root / ACTIVE_RUN_FILE)
    if not active:
        return
    run_id = str(active.get("run_id", ""))
    store = find_run_store(root, run_id=run_id)
    status = read_json(store.status_path) or {}
    if status.get("status") in {
        phase.value for phase in RunPhase if phase.active
    }:
        raise EvidenceError(
            f"active run must be stopped or resolved first: {run_id}"
        )
    store.clear_active()


def classify_outcome(
    *,
    stop_requested: bool,
    task_completed: bool,
    task_errors: list[dict[str, Any]],
    connection_errors: list[dict[str, Any]] | None = None,
    terminal_state: str | None = None,
    verification_status: str | None,
) -> RunPhase:
    """Map task callbacks and visual evidence without conflating success."""

    if stop_requested or terminal_state == "STOPPED":
        return RunPhase.STOPPED
    if connection_errors or terminal_state == "DISCONNECTED":
        return RunPhase.FAILED
    if task_errors:
        return RunPhase.FAILED
    if not task_completed:
        return RunPhase.UNKNOWN
    if verification_status == "three_star":
        return RunPhase.SUCCEEDED
    if verification_status in {"two_star", "non_three_star"}:
        return RunPhase.FAILED
    return RunPhase.UNKNOWN
