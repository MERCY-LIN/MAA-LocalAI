import json

import pytest

from autopush.metrics import (
    BudgetExceeded,
    BudgetLimits,
    RoundMetrics,
    TestResult as ValidationResult,
)


def test_budget_enforcement_and_report(tmp_path):
    metrics = RoundMetrics(
        round_name="unit",
        stage="TL-8",
        limits=BudgetLimits(
            computer_use_operations=1,
            screenshots=1,
            tool_calls=2,
            deepseek_input_tokens=10,
            deepseek_output_tokens=5,
            strategy_runs=1,
        ),
    )
    metrics.record_computer_use()
    metrics.record_screenshot()
    metrics.record_tool_call()
    metrics.record_model_usage(input_tokens=7, output_tokens=3)
    metrics.record_tool_call()
    metrics.add_test(ValidationResult(name="ok", success=True))
    metrics.finish()

    report = metrics.write(tmp_path / "report.json")
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["counters"]["computer_use_operations"] == 1
    assert payload["counters"]["deepseek_input_tokens"] == 7
    assert payload["summary"] == {"passed": 1, "failed": 0}

    with pytest.raises(BudgetExceeded):
        metrics.record_screenshot()


def test_computer_use_is_recorded_without_a_hard_cap_by_default():
    metrics = RoundMetrics(round_name="unbounded-computer-use", stage="test")

    metrics.record_computer_use(12)

    assert metrics.limits.computer_use_operations is None
    assert metrics.counters["computer_use_operations"] == 12
