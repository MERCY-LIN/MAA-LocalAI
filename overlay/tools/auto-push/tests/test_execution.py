from argparse import Namespace

import pytest

from autopush.execution import (
    DEFAULT_VERIFY_THRESHOLD,
    StageExecutionConfig,
)


def _arguments(**updates):
    values = {
        "stage": "OF-1",
        "copilot": "job.json",
        "package_dir": "package",
        "evidence_root": "evidence",
        "verify_threshold": 0.91,
        "timeout": 600,
    }
    values.update(updates)
    return Namespace(**values)


def test_execution_config_supplies_shared_defaults():
    config = StageExecutionConfig.from_arguments(_arguments())

    assert config.verify_threshold == 0.91
    assert config.ignore_requirements is False
    assert config.formation is False
    assert config.validate() is None


def test_execution_config_uses_safe_threshold_default_when_omitted():
    arguments = _arguments()
    del arguments.verify_threshold

    config = StageExecutionConfig.from_arguments(arguments)

    assert config.verify_threshold == DEFAULT_VERIFY_THRESHOLD


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"stage": " "}, "stage"),
        ({"copilot": ""}, "copilot"),
        ({"verify_threshold": 0.0}, "verify_threshold"),
        ({"verify_threshold": 1.1}, "verify_threshold"),
        ({"timeout": 0}, "timeout"),
        ({"formation_index": -1}, "formation_index"),
        ({"support_unit_usage": 3}, "support_unit_usage"),
    ],
)
def test_execution_config_rejects_invalid_values(updates, message):
    config = StageExecutionConfig.from_arguments(_arguments(**updates))

    with pytest.raises(ValueError, match=message):
        config.validate()
