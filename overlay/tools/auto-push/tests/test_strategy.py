from pathlib import Path

from autopush.strategy import (
    CopilotStrategy,
    OperatorRequirement,
    RosterOperator,
    evaluate_strategy,
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
