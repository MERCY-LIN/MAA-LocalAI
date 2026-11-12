"""Bounded strategy scheduling with optional drills and result verification."""

from __future__ import annotations

from dataclasses import dataclass, field

from .metrics import RoundMetrics, TestResult
from .states import RunPhase
from .strategy import CandidateEvaluation
from .verifier import ResultVerification


@dataclass(frozen=True)
class TrialAttempt:
    strategy_index: int
    strategy_path: str
    mode: str


@dataclass(frozen=True)
class AttemptRecord:
    attempt: TrialAttempt
    verification: ResultVerification


@dataclass
class TrialPolicy:
    max_attempts: int = 6
    use_drill_first: bool = False
    max_unknown_verifications: int = 2
    max_computer_use_operations: int | None = None


@dataclass
class TrialScheduler:
    candidates: list[CandidateEvaluation]
    metrics: RoundMetrics
    policy: TrialPolicy = field(default_factory=TrialPolicy)
    next_strategy_index: int = 0
    current: TrialAttempt | None = None
    drill_verified_index: int | None = None
    attempts: list[AttemptRecord] = field(default_factory=list)
    unknown_verifications: int = 0
    computer_use_operations: int = 0
    phase: RunPhase = RunPhase.PREPARING
    terminal_reason: str | None = None
    last_verification: ResultVerification | None = None

    @property
    def completed(self) -> bool:
        return self.phase is RunPhase.SUCCEEDED

    def terminate(self, phase: RunPhase, reason: str) -> None:
        if not phase.terminal:
            raise ValueError(f"terminal phase required: {phase}")
        self.phase = phase
        self.terminal_reason = reason

    def record_stop(self, reason: str = "MaaCore task stopped") -> None:
        self.terminate(RunPhase.STOPPED, reason)

    def record_failure(self, reason: str) -> None:
        self.terminate(RunPhase.FAILED, reason)

    def record_pause(self, reason: str = "execution paused") -> None:
        self.terminate(RunPhase.PAUSED, reason)

    def next_attempt(self) -> TrialAttempt | None:
        if self.phase.terminal:
            return None
        if len(self.attempts) >= self.policy.max_attempts:
            self.terminate(
                RunPhase.BUDGET_EXHAUSTED,
                "attempt budget exhausted",
            )
            return None
        if self.drill_verified_index is not None:
            candidate = self.candidates[self.drill_verified_index]
            attempt = TrialAttempt(
                strategy_index=self.drill_verified_index,
                strategy_path=candidate.strategy.strategy_id,
                mode="real",
            )
            self.current = attempt
            self.phase = RunPhase.PREPARING
            return attempt

        while self.next_strategy_index < len(self.candidates):
            index = self.next_strategy_index
            self.next_strategy_index += 1
            candidate = self.candidates[index]
            if not candidate.runnable:
                continue
            attempt = TrialAttempt(
                strategy_index=index,
                strategy_path=candidate.strategy.strategy_id,
                mode="drill" if self.policy.use_drill_first else "real",
            )
            self.current = attempt
            self.phase = RunPhase.PREPARING
            return attempt
        self.terminate(RunPhase.FAILED, "no runnable candidate")
        return None

    def record_verification(
        self,
        verification: ResultVerification,
    ) -> None:
        if self.current is None:
            raise ValueError("no active trial attempt")
        record = AttemptRecord(self.current, verification)
        self.attempts.append(record)
        self.last_verification = verification

        if verification.status == "unknown":
            self.phase = RunPhase.UNKNOWN
            self.unknown_verifications += 1
            self.metrics.add_test(
                TestResult(
                    name="result-verification",
                    success=False,
                    detail="unknown screen; Computer Use verification is permitted",
                    strategy_path=self.current.strategy_path,
                )
            )
            if self.unknown_verifications > self.policy.max_unknown_verifications:
                self.terminate(
                    RunPhase.UNKNOWN,
                    "unknown verification limit reached",
                )
            else:
                self.phase = RunPhase.PREPARING
            self.current = None
            return

        if self.current.mode == "drill":
            if verification.status == "three_star":
                self.drill_verified_index = self.current.strategy_index
                self.phase = RunPhase.PREPARING
                self.metrics.add_test(
                    TestResult(
                        name="drill-three-star",
                        success=True,
                        detail="drill validated; real run still required",
                        strategy_path=self.current.strategy_path,
                        stars=3,
                    )
                )
            else:
                self.phase = RunPhase.PREPARING
                self.metrics.add_test(
                    TestResult(
                        name="drill-non-three-star",
                        success=False,
                        detail=verification.status,
                        strategy_path=self.current.strategy_path,
                        stars=verification.stars,
                    )
                )
        else:
            success = verification.status == "three_star"
            self.metrics.add_test(
                TestResult(
                    name="real-first-clear",
                    success=success,
                    detail=verification.status,
                    strategy_path=self.current.strategy_path,
                    stars=verification.stars,
                )
            )
            if success:
                self.terminate(
                    RunPhase.SUCCEEDED,
                    "three-star first clear",
                )
            else:
                self.phase = RunPhase.PREPARING

        self.current = None

    def authorize_computer_use(self) -> bool:
        return self.authorize_computer_use_for(self.last_verification)

    def authorize_computer_use_for(
        self,
        verification: ResultVerification | None,
    ) -> bool:
        limits = [
            limit
            for limit in (
                self.policy.max_computer_use_operations,
                self.metrics.limits.computer_use_operations,
            )
            if limit is not None
        ]
        effective_limit = min(limits) if limits else None
        used = max(
            self.computer_use_operations,
            self.metrics.counters["computer_use_operations"],
        )
        if (
            verification is None
            or not verification.needs_computer_use
            or (effective_limit is not None and used >= effective_limit)
        ):
            return False
        self.computer_use_operations += 1
        self.metrics.record_computer_use()
        return True
