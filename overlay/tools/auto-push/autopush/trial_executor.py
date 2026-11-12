"""Injected trial executor connecting navigation, MAA and result verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from .maa_core import MaaEvent, summarize_events
from .states import RunPhase
from .trial import AttemptRecord, TrialAttempt, TrialScheduler
from .verifier import ResultVerification


@dataclass(frozen=True)
class TrialExecutionReport:
    completed: bool
    phase: RunPhase
    terminal_reason: str
    attempts: tuple[AttemptRecord, ...]
    stopped_reason: str


class TrialExecutor:
    """Run the bounded trial loop with injected local adapters."""

    def __init__(
        self,
        scheduler: TrialScheduler,
        *,
        prepare_stage: Callable[[TrialAttempt], None],
        run_copilot: Callable[[TrialAttempt], list[MaaEvent]],
        capture_result: Callable[[], np.ndarray],
        verify_result: Callable[[np.ndarray], ResultVerification],
        computer_use_verify: Callable[[], ResultVerification | None] | None = None,
    ):
        self.scheduler = scheduler
        self.prepare_stage = prepare_stage
        self.run_copilot = run_copilot
        self.capture_result = capture_result
        self.verify_result = verify_result
        self.computer_use_verify = computer_use_verify

    def run(self) -> TrialExecutionReport:
        while True:
            attempt = self.scheduler.next_attempt()
            if attempt is None:
                break
            self.scheduler.phase = RunPhase.PREPARING
            try:
                self.prepare_stage(attempt)
            except Exception as exception:  # noqa: BLE001 - injected boundary
                self.scheduler.record_failure(
                    f"stage preparation failed: {exception}"
                )
                break

            self.scheduler.phase = RunPhase.RUNNING
            try:
                events = self.run_copilot(attempt)
            except Exception as exception:  # noqa: BLE001 - injected boundary
                self.scheduler.record_failure(
                    f"MaaCore execution failed: {exception}"
                )
                break
            summary = summarize_events(events)
            terminal_state = summary["terminal_state"]
            if terminal_state == "STOPPED":
                self.scheduler.record_stop("MaaCore task stopped")
                break
            if terminal_state == "DISCONNECTED":
                self.scheduler.record_failure(
                    "MaaCore connection lost"
                )
                break
            if terminal_state == "FAILED" or summary["task_errors"]:
                self.scheduler.record_failure("MaaCore task error")
                break
            if not summary["task_completed"]:
                self.scheduler.record_failure(
                    f"unexpected task terminal state: {terminal_state}"
                )
                break

            self.scheduler.phase = RunPhase.VERIFYING
            try:
                verification = self.verify_result(self.capture_result())
            except Exception as exception:  # noqa: BLE001 - injected boundary
                self.scheduler.record_failure(
                    f"result capture or verification failed: {exception}"
                )
                break
            if (
                verification.status == "unknown"
                and self.computer_use_verify is not None
                and self.scheduler.authorize_computer_use_for(verification)
            ):
                fallback = self.computer_use_verify()
                if fallback is not None:
                    verification = fallback

            self.scheduler.record_verification(verification)
            if self.scheduler.phase.terminal:
                break
        terminal_reason = (
            self.scheduler.terminal_reason
            or "attempt budget exhausted"
        )
        return TrialExecutionReport(
            completed=self.scheduler.completed,
            phase=self.scheduler.phase,
            terminal_reason=terminal_reason,
            attempts=tuple(self.scheduler.attempts),
            stopped_reason=terminal_reason,
        )
