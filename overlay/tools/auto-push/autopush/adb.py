"""Small ADB controller for repeatable emulator operations."""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field as dataclass_field, replace
from datetime import UTC, datetime
from pathlib import Path

from .device_control import DeviceControlLease
from .metrics import RoundMetrics
from .recognizer import decode_png


class AdbError(RuntimeError):
    """Raised when an ADB command fails."""


@dataclass(frozen=True)
class CaptureFrame:
    payload: bytes
    sequence: int
    action_sequence: int
    captured_at: str
    cache_hit: bool = False

    def metadata(self) -> dict[str, str | int | bool]:
        return {
            "sequence": self.sequence,
            "action_sequence": self.action_sequence,
            "captured_at": self.captured_at,
            "cache_hit": self.cache_hit,
        }


@dataclass
class AdbClient:
    """Thin wrapper that records every repeated device operation."""

    adb_path: str | Path
    serial: str = "emulator-5554"
    metrics: RoundMetrics | None = None
    timeout_seconds: int = 30
    device_control_timeout_seconds: float = 5.0
    minimum_capture_interval_seconds: float = 1.5
    _capture_lock: threading.Lock = dataclass_field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )
    _last_capture_at: float = dataclass_field(default=0.0, init=False)
    _last_capture: bytes | None = dataclass_field(default=None, init=False)
    _last_frame: CaptureFrame | None = dataclass_field(default=None, init=False)
    _capture_sequence: int = dataclass_field(default=0, init=False)
    _action_sequence: int = dataclass_field(default=0, init=False)

    def _command(self, *arguments: str) -> list[str]:
        return [
            str(self.adb_path),
            "-s",
            self.serial,
            *arguments,
        ]

    def _run(self, *arguments: str) -> bytes:
        if self.metrics is not None:
            self.metrics.record_tool_call()
        completed = subprocess.run(
            self._command(*arguments),
            check=False,
            capture_output=True,
            timeout=self.timeout_seconds,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise AdbError(stderr or f"ADB exited with {completed.returncode}")
        return completed.stdout

    def _run_controlled(self, *arguments: str) -> bytes:
        with DeviceControlLease(
            self.serial,
            timeout_seconds=self.device_control_timeout_seconds,
        ):
            return self._run(*arguments)

    def devices(self) -> list[str]:
        output = self._run("devices").decode("utf-8", errors="replace")
        serials: list[str] = []
        for line in output.splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 2 and fields[1] == "device":
                serials.append(fields[0])
        return serials

    def capture_frame(self, *, force: bool = False) -> CaptureFrame:
        with self._capture_lock:
            now = time.monotonic()
            if (
                not force
                and self._last_frame is not None
                and now - self._last_capture_at
                < self.minimum_capture_interval_seconds
            ):
                return replace(self._last_frame, cache_hit=True)
            if self.metrics is not None:
                self.metrics.record_screenshot()
            payload = self._run_controlled("exec-out", "screencap", "-p")
            self._capture_sequence += 1
            frame = CaptureFrame(
                payload=payload,
                sequence=self._capture_sequence,
                action_sequence=self._action_sequence,
                captured_at=datetime.now(UTC).isoformat(),
            )
            self._last_capture = payload
            self._last_frame = frame
            self._last_capture_at = now
            return frame

    def capture_png(self, *, force: bool = False) -> bytes:
        return self.capture_frame(force=force).payload

    def capture_bgr(self):
        return decode_png(self.capture_png())

    def _invalidate_before_action(self) -> None:
        with self._capture_lock:
            self._action_sequence += 1
            self._last_capture = None
            self._last_frame = None
            self._last_capture_at = 0.0

    def click(self, x: int, y: int) -> None:
        self._invalidate_before_action()
        self._run_controlled("shell", "input", "tap", str(x), str(y))

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 300,
    ) -> None:
        self._invalidate_before_action()
        self._run_controlled(
            "shell",
            "input",
            "swipe",
            str(start_x),
            str(start_y),
            str(end_x),
            str(end_y),
            str(duration_ms),
        )

    def back(self) -> None:
        self._invalidate_before_action()
        self._run_controlled("shell", "input", "keyevent", "4")
