"""Per-round budgets and auditable metrics for auto-push tests."""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class BudgetExceeded(RuntimeError):
    """Raised when a test round exceeds a configured hard budget."""


@dataclass(frozen=True)
class BudgetLimits:
    """Hard limits for one auto-push research round."""

    computer_use_operations: int | None = None
    screenshots: int = 40
    tool_calls: int = 200
    deepseek_input_tokens: int = 12_000
    deepseek_output_tokens: int = 4_000
    strategy_runs: int = 6


@dataclass(frozen=True)
class TestResult:
    """One strategy trial or validation result."""

    name: str
    success: bool
    detail: str = ""
    strategy_path: str | None = None
    stars: int | None = None


@dataclass
class RoundMetrics:
    """Thread-safe counters and results for one test round."""

    round_name: str
    stage: str
    limits: BudgetLimits = field(default_factory=BudgetLimits)
    round_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    counters: dict[str, int] = field(
        default_factory=lambda: {
            "computer_use_operations": 0,
            "screenshots": 0,
            "tool_calls": 0,
            "deepseek_input_tokens": 0,
            "deepseek_output_tokens": 0,
            "strategy_runs": 0,
        }
    )
    tests: list[TestResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    @property
    def limits_dict(self) -> dict[str, int]:
        return asdict(self.limits)

    def consume(self, counter: str, amount: int = 1) -> int:
        """Consume a budgeted counter and return the new total."""

        if amount < 0:
            raise ValueError("amount must be non-negative")
        if counter not in self.counters:
            raise KeyError(f"unknown counter: {counter}")

        limit = getattr(self.limits, counter)
        with self._lock:
            next_value = self.counters[counter] + amount
            if limit is not None and next_value > limit:
                raise BudgetExceeded(
                    f"{counter} budget exceeded: {next_value} > {limit}"
                )
            self.counters[counter] = next_value
            return next_value

    def record_tool_call(self, amount: int = 1) -> None:
        self.consume("tool_calls", amount)

    def record_screenshot(self, amount: int = 1) -> None:
        self.consume("screenshots", amount)

    def record_computer_use(self, amount: int = 1) -> None:
        self.consume("computer_use_operations", amount)

    def record_model_usage(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        self.consume("deepseek_input_tokens", input_tokens)
        self.consume("deepseek_output_tokens", output_tokens)

    def add_test(self, result: TestResult) -> None:
        with self._lock:
            self.tests.append(result)

    def add_note(self, note: str) -> None:
        with self._lock:
            self.notes.append(note)

    def finish(self) -> None:
        with self._lock:
            if self.finished_at is None:
                self.finished_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "round_id": self.round_id,
                "round_name": self.round_name,
                "stage": self.stage,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "limits": self.limits_dict,
                "counters": dict(self.counters),
                "tests": [asdict(item) for item in self.tests],
                "summary": {
                    "passed": sum(item.success for item in self.tests),
                    "failed": sum(not item.success for item in self.tests),
                },
                "notes": list(self.notes),
            }

    def write(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, destination)
        return destination
