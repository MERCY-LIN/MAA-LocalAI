"""Explicit run phases shared by execution, verification, and evidence."""

from __future__ import annotations

from enum import Enum


class RunPhase(str, Enum):
    """One phase in a formally executed combat run."""

    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    STOPPING = "STOPPING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"
    PAUSED = "PAUSED"
    UNKNOWN = "UNKNOWN"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"

    @property
    def terminal(self) -> bool:
        return self in {
            RunPhase.SUCCEEDED,
            RunPhase.FAILED,
            RunPhase.STOPPED,
            RunPhase.PAUSED,
            RunPhase.UNKNOWN,
            RunPhase.BUDGET_EXHAUSTED,
        }

    @property
    def active(self) -> bool:
        return self in {
            RunPhase.PREPARING,
            RunPhase.RUNNING,
            RunPhase.VERIFYING,
            RunPhase.STOPPING,
        }


def phase_from_status(value: RunPhase | str) -> RunPhase:
    if isinstance(value, RunPhase):
        return value
    return RunPhase(value)
