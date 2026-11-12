"""State-gated ADB navigation that avoids acting on transition screens."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .adb import AdbClient
from .metrics import RoundMetrics
from .recognizer import decode_png
from .state_memory import StateMatch, VisualStateMemory


class NavigationError(RuntimeError):
    """Raised when a screen is not recognized or an allowed precondition fails."""


@dataclass
class AdbStateNavigator:
    client: AdbClient
    memory: VisualStateMemory
    metrics: RoundMetrics
    poll_interval_seconds: float = 1.0
    last_frame_metadata: dict[str, object] | None = None

    def observe(self) -> StateMatch:
        frame = self.client.capture_frame()
        self.last_frame_metadata = frame.metadata()
        return self.memory.recognize(
            decode_png(frame.payload),
            metrics=self.metrics,
        )

    def wait_for_known(
        self,
        *,
        timeout_seconds: float = 10.0,
        allowed_states: set[str] | None = None,
    ) -> StateMatch:
        deadline = time.monotonic() + timeout_seconds
        last_match = StateMatch(
            name="unknown",
            distance=64,
            confidence=0.0,
            known=False,
            reference_path=None,
        )
        while time.monotonic() < deadline:
            last_match = self.observe()
            if (
                last_match.known
                and (
                    allowed_states is None
                    or last_match.name in allowed_states
                )
            ):
                return last_match
            time.sleep(self.poll_interval_seconds)
        raise NavigationError(
            f"stable screen not reached: state={last_match.name} "
            f"distance={last_match.distance}"
        )

    def safe_tap(
        self,
        x: int,
        y: int,
        *,
        allowed_states: set[str],
        post_action_states: set[str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> StateMatch:
        current = self.wait_for_known(
            timeout_seconds=timeout_seconds,
            allowed_states=allowed_states,
        )
        self.client.click(x, y)
        if post_action_states is None:
            return self.wait_for_known(timeout_seconds=timeout_seconds)
        return self.wait_for_known(
            timeout_seconds=timeout_seconds,
            allowed_states=post_action_states,
        )


def run_when_idle(
    *,
    navigator: AdbStateNavigator,
    allowed_states: set[str],
    action: Callable[[], None],
) -> StateMatch:
    """Run one offline-safe action only after a stable allowed state."""

    state = navigator.wait_for_known(allowed_states=allowed_states)
    action()
    return state
