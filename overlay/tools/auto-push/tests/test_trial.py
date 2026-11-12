from pathlib import Path

from autopush.metrics import BudgetLimits, RoundMetrics
from autopush.states import RunPhase
from autopush.strategy import CopilotStrategy, CandidateEvaluation
from autopush.trial import TrialPolicy, TrialScheduler
from autopush.verifier import ResultVerification


def _candidate(name: str) -> CandidateEvaluation:
    strategy = CopilotStrategy(
        path=Path(name),
        stage_id="test",
        title=name,
        action_count=3,
    )
    return CandidateEvaluation(
        strategy=strategy,
        status="ready",
        score=100,
        missing=(),
        substitutions=(),
        support_needed=False,
        diagnostics=(),
    )


def _result(status: str, stars: int | None) -> ResultVerification:
    return ResultVerification(
        status=status,
        stars=stars,
        confidence=0.95,
        evidence=(),
        needs_computer_use=status == "unknown",
    )


def test_optional_drill_must_pass_before_real_run_when_explicitly_enabled():
    metrics = RoundMetrics(
        round_name="trial",
        stage="test",
        limits=BudgetLimits(computer_use_operations=2),
    )
    scheduler = TrialScheduler(
        [_candidate("a.json"), _candidate("b.json")],
        metrics,
        TrialPolicy(use_drill_first=True),
    )

    first = scheduler.next_attempt()
    assert first is not None and first.mode == "drill"
    scheduler.record_verification(_result("non_three_star", 2))

    second = scheduler.next_attempt()
    assert second is not None and second.mode == "drill"
    scheduler.record_verification(_result("three_star", 3))

    real = scheduler.next_attempt()
    assert real is not None and real.mode == "real"
    assert real.strategy_index == second.strategy_index
    scheduler.record_verification(_result("three_star", 3))
    assert scheduler.completed
    assert scheduler.phase is RunPhase.SUCCEEDED


def test_default_policy_starts_with_formal_battle():
    metrics = RoundMetrics(round_name="trial-default", stage="test")
    scheduler = TrialScheduler(
        [_candidate("a.json")],
        metrics,
    )

    first = scheduler.next_attempt()

    assert first is not None
    assert first.mode == "real"


def test_attempt_budget_exhaustion_has_a_distinct_terminal_phase():
    scheduler = TrialScheduler(
        [_candidate("a.json")],
        RoundMetrics(round_name="trial-budget", stage="test"),
        TrialPolicy(max_attempts=1),
    )
    assert scheduler.next_attempt() is not None
    scheduler.record_verification(_result("two_star", 2))

    assert scheduler.next_attempt() is None
    assert scheduler.phase is RunPhase.BUDGET_EXHAUSTED
    assert not scheduler.completed


def test_computer_use_fallback_is_budgeted():
    metrics = RoundMetrics(
        round_name="trial",
        stage="test",
        limits=BudgetLimits(computer_use_operations=1),
    )
    scheduler = TrialScheduler(
        [_candidate("a.json")],
        metrics,
        TrialPolicy(max_unknown_verifications=2, max_computer_use_operations=1),
    )
    assert scheduler.next_attempt() is not None
    scheduler.record_verification(_result("unknown", None))
    assert scheduler.authorize_computer_use()
    assert not scheduler.authorize_computer_use()
