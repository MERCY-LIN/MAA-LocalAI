from pathlib import Path

import numpy as np

from autopush.maa_core import CoreMessage, MaaEvent
from autopush.metrics import BudgetLimits, RoundMetrics
from autopush.states import RunPhase
from autopush.strategy import CopilotStrategy, CandidateEvaluation
from autopush.trial import TrialPolicy, TrialScheduler
from autopush.trial_executor import TrialExecutor
from autopush.verifier import ResultVerification


def candidate(name: str) -> CandidateEvaluation:
    return CandidateEvaluation(
        strategy=CopilotStrategy(
            path=Path(name),
            stage_id="stage",
            title=name,
            action_count=4,
        ),
        status="ready",
        score=100,
        missing=(),
        substitutions=(),
        support_needed=False,
        diagnostics=(),
    )


def verification(status: str, stars: int | None) -> ResultVerification:
    return ResultVerification(
        status=status,
        stars=stars,
        confidence=0.95,
        evidence=(),
        needs_computer_use=status == "unknown",
    )


def events(*messages: CoreMessage, task_id: int = 1) -> list[MaaEvent]:
    return [
        MaaEvent(
            message,
            {"taskchain": "Copilot", "taskid": task_id},
        )
        for message in messages
    ]


def test_executor_switches_route_after_failed_drill():
    metrics = RoundMetrics(
        round_name="executor",
        stage="stage",
        limits=BudgetLimits(computer_use_operations=1),
    )
    scheduler = TrialScheduler(
        [candidate("first.json"), candidate("second.json")],
        metrics,
        TrialPolicy(use_drill_first=True, max_attempts=4),
    )
    prepared_modes = []
    executed_paths = []
    verifications = iter(
        [
            verification("non_three_star", 2),
            verification("three_star", 3),
            verification("three_star", 3),
        ]
    )

    def run_copilot(attempt):
        executed_paths.append((attempt.strategy_path, attempt.mode))
        return events(
            CoreMessage.TASK_CHAIN_START,
            CoreMessage.TASK_CHAIN_COMPLETED,
        )

    executor = TrialExecutor(
        scheduler,
        prepare_stage=lambda attempt: prepared_modes.append(attempt.mode),
        run_copilot=run_copilot,
        capture_result=lambda: np.zeros((720, 1280, 3), dtype=np.uint8),
        verify_result=lambda _image: next(verifications),
    )
    report = executor.run()

    assert report.completed
    assert report.phase is RunPhase.SUCCEEDED
    assert report.stopped_reason == "three-star first clear"
    assert prepared_modes == ["drill", "drill", "real"]
    assert executed_paths[-1][1] == "real"
    assert "second.json" in executed_paths[-1][0]


def test_executor_uses_budgeted_computer_use_only_for_unknown():
    metrics = RoundMetrics(
        round_name="executor",
        stage="stage",
        limits=BudgetLimits(computer_use_operations=1),
    )
    scheduler = TrialScheduler(
        [candidate("first.json")],
        metrics,
        TrialPolicy(use_drill_first=True, max_attempts=2),
    )
    fallback_calls = 0

    def fallback():
        nonlocal fallback_calls
        fallback_calls += 1
        return verification("three_star", 3)

    executor = TrialExecutor(
        scheduler,
        prepare_stage=lambda _attempt: None,
        run_copilot=lambda _attempt: events(
            CoreMessage.TASK_CHAIN_START,
            CoreMessage.TASK_CHAIN_COMPLETED,
        ),
        capture_result=lambda: np.zeros((720, 1280, 3), dtype=np.uint8),
        verify_result=lambda _image: verification("unknown", None),
        computer_use_verify=fallback,
    )
    report = executor.run()

    assert not report.completed
    assert report.stopped_reason == "attempt budget exhausted"
    assert fallback_calls == 1
    assert metrics.counters["computer_use_operations"] == 1


def test_executor_stops_without_capturing_result_after_task_stop():
    scheduler = TrialScheduler(
        [candidate("first.json")],
        RoundMetrics(round_name="executor-stop", stage="stage"),
    )
    capture_calls = 0

    def capture():
        nonlocal capture_calls
        capture_calls += 1
        return np.zeros((720, 1280, 3), dtype=np.uint8)

    executor = TrialExecutor(
        scheduler,
        prepare_stage=lambda _attempt: None,
        run_copilot=lambda _attempt: events(
            CoreMessage.TASK_CHAIN_START,
            CoreMessage.TASK_CHAIN_STOPPED,
        ),
        capture_result=capture,
        verify_result=lambda _image: verification("three_star", 3),
    )

    report = executor.run()

    assert report.phase is RunPhase.STOPPED
    assert not report.completed
    assert capture_calls == 0


def test_executor_fails_immediately_on_disconnect():
    scheduler = TrialScheduler(
        [candidate("first.json")],
        RoundMetrics(round_name="executor-disconnect", stage="stage"),
    )
    executor = TrialExecutor(
        scheduler,
        prepare_stage=lambda _attempt: None,
        run_copilot=lambda _attempt: [
            MaaEvent(
                CoreMessage.TASK_CHAIN_START,
                {"taskchain": "Copilot", "taskid": 1},
            ),
            MaaEvent(
                CoreMessage.CONNECTION_INFO,
                {"what": "Disconnect"},
            ),
        ],
        capture_result=lambda: np.zeros((720, 1280, 3), dtype=np.uint8),
        verify_result=lambda _image: verification("three_star", 3),
    )

    report = executor.run()

    assert report.phase is RunPhase.FAILED
    assert "connection lost" in report.terminal_reason
