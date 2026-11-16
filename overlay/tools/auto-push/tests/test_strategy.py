from pathlib import Path

from autopush.strategy import (
    CandidateEvaluation,
    CopilotStrategy,
    OperatorRequirement,
    RosterOperator,
    candidate_has_hard_elite_gap,
    candidate_has_hard_requirement_gap,
    evaluate_strategy,
    job_is_raid_only,
    job_requires_wartime_press,
)


def _strategy(*names: str) -> CopilotStrategy:
    return CopilotStrategy(
        path=Path("strategy.json"),
        stage_id="act55side_08",
        title="test",
        direct_operators=[
            OperatorRequirement(
                name=name,
                elite=2,
                level=60,
                skill_level=10,
                requirements_declared=True,
            )
            for name in names
        ],
        action_count=5,
    )


def test_ready_strategy():
    roster = {
        "A": RosterOperator("A", elite=2, level=90, skill_level=10),
        "B": RosterOperator("B", elite=2, level=90, skill_level=10),
    }
    result = evaluate_strategy(_strategy("A", "B"), roster)
    assert result.status == "ready"
    assert result.runnable


def test_single_missing_operator_can_use_support():
    roster = {
        "A": RosterOperator("A", elite=2, level=90, skill_level=10),
    }
    result = evaluate_strategy(_strategy("A", "B"), roster, allow_support=True)
    assert result.status == "support"
    assert result.support_needed
    assert result.missing == ("B",)


def test_substitution_is_local_and_explicit():
    roster = {
        "A": RosterOperator("A", elite=2, level=90, skill_level=10),
        "Alternative": RosterOperator("Alternative", elite=2, level=90, skill_level=10),
    }
    result = evaluate_strategy(
        _strategy("A", "B"),
        roster,
        substitutions={"B": ["Alternative"]},
    )
    assert result.status == "substitution"
    assert result.substitutions == (("B", "Alternative"),)


def test_two_missing_operators_are_unavailable():
    result = evaluate_strategy(_strategy("A", "B"), {})
    assert result.status == "unavailable"
    assert not result.runnable


def test_level_and_skill_gaps_are_hard_requirement_failures():
    roster = {
        "A": RosterOperator("A", elite=2, level=1, skill_level=1),
    }
    result = evaluate_strategy(_strategy("A"), roster, allow_support=False)

    assert result.status == "degraded"
    assert not result.requirements_satisfied
    assert candidate_has_hard_requirement_gap(result)


def test_two_groups_cannot_both_reuse_one_operator():
    strategy = CopilotStrategy(
        path=Path("strategy.json"),
        stage_id="act55side_08",
        title="test",
        operator_groups=[
            [
                OperatorRequirement(
                    name="A",
                    elite=2,
                    level=60,
                    skill_level=10,
                    requirements_declared=True,
                )
            ],
            [
                OperatorRequirement(
                    name="A",
                    elite=2,
                    level=60,
                    skill_level=10,
                    requirements_declared=True,
                )
            ],
        ],
    )
    roster = {
        "A": RosterOperator("A", elite=2, level=90, skill_level=10),
    }

    result = evaluate_strategy(strategy, roster, allow_support=False)

    assert result.status == "unavailable"
    assert not result.requirements_satisfied
    assert any("lineup conflict" in item for item in result.diagnostics)


def test_group_assignment_produces_a_unique_concrete_lineup():
    strategy = CopilotStrategy(
        path=Path("strategy.json"),
        stage_id="act55side_08",
        title="test",
        operator_groups=[
            [
                OperatorRequirement("A", 2, 60, 10, requirements_declared=True),
                OperatorRequirement("B", 2, 60, 10, requirements_declared=True),
            ],
            [
                OperatorRequirement("A", 2, 60, 10, requirements_declared=True),
            ],
        ],
    )
    roster = {
        "A": RosterOperator("A", elite=2, level=90, skill_level=10),
        "B": RosterOperator("B", elite=2, level=90, skill_level=10),
    }

    result = evaluate_strategy(strategy, roster, allow_support=False)

    assert result.status == "ready"
    assert result.requirements_satisfied
    assert {slot.assigned for slot in result.lineup} == {"A", "B"}


def test_substitution_does_not_hide_a_remaining_missing_operator():
    roster = {
        "A": RosterOperator("A", elite=2, level=90, skill_level=10),
        "Alternative": RosterOperator(
            "Alternative",
            elite=2,
            level=90,
            skill_level=10,
        ),
    }

    result = evaluate_strategy(
        _strategy("A", "B", "C"),
        roster,
        substitutions={"B": ["Alternative"]},
        allow_support=False,
    )

    assert result.status == "unavailable"
    assert result.missing == ("C",)
    assert not result.requirements_satisfied


def test_job_prerequisites_and_mode_are_detected():
    payload = {
        "doc": {
            "title": "TL-EX-5 仅突袭",
            "details": "需要先做完报纸小游戏使自爆榴弹初始费用降为10费",
        }
    }

    assert job_requires_wartime_press(payload)
    assert job_is_raid_only(payload)


def test_hard_elite_gap_is_detected_from_diagnostics():
    candidate = CandidateEvaluation(
        strategy=CopilotStrategy(
            path=Path("job.json"),
            stage_id="act55side_ex05",
            title="test",
        ),
        status="degraded",
        score=0,
        missing=(),
        substitutions=(),
        support_needed=False,
        diagnostics=("A: elite 1 < 2",),
    )

    assert candidate_has_hard_elite_gap(candidate)
