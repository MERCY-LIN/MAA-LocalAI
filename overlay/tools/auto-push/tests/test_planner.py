from autopush.map_planner import StageMap
from autopush.metrics import RoundMetrics
from dataclasses import replace
from pathlib import Path

from autopush.planner import build_round_plan, choose_strategy_with_model
from autopush.strategy import (
    CandidateEvaluation,
    CopilotStrategy,
    RosterOperator,
    evaluate_strategy,
)
from autopush.model_client import ModelError, ModelReply


def test_plan_policy_does_not_require_a_drill_before_formal_battle():
    plan = build_round_plan(
        stage_id="act55side_ex04",
        stage_map=StageMap(
            stage_id="act55side_ex04",
            stage_code="TL-EX-4",
            name="test",
            width=0,
            height=0,
            tiles=[],
            starts=[],
            ends=[],
            route_tiles=[],
            choke_points=[],
            candidates=[],
        ),
        strategy_paths=[],
        roster={},
        substitutions={},
        metrics=RoundMetrics(
            round_name="planner-policy",
            stage="act55side_ex04",
        ),
    )

    policy = plan["execution"]["trial_policy"]
    assert policy["use_drill_first"] is False
    assert policy["require_three_star_before_real_run"] is False
    assert policy["require_three_star_after_real_run"] is True


class _FakeModel:
    def __init__(self, content=None, exception=None):
        self.content = content
        self.exception = exception

    def complete_json(self, **_kwargs):
        if self.exception is not None:
            raise self.exception
        return ModelReply(content=self.content, input_tokens=1, output_tokens=1)


def _candidate():
    return CandidateEvaluation(
        strategy=type(
            "Strategy",
            (),
            {
                "strategy_id": "candidate.json",
                "title": "candidate",
                "action_count": 1,
            },
        )(),
        status="runnable",
        score=1.0,
        missing=[],
        substitutions=[],
        diagnostics=[],
        support_needed=False,
    )


def test_model_selector_accepts_fenced_json_with_trailing_text():
    selection = choose_strategy_with_model(
        _FakeModel(
            content='```json\n{"candidate":1,"reason":"ok"}\n```\nextra'
        ),
        [_candidate()],
        stage_id="act55side_ex05",
        metrics=RoundMetrics(round_name="model", stage="act55side_ex05"),
    )

    assert selection == 0


def test_model_selection_failure_falls_back_to_local_ranking(
    monkeypatch,
):
    stage_map = StageMap(
        stage_id="act55side_ex05",
        stage_code="TL-EX-5",
        name="test",
        width=0,
        height=0,
        tiles=[],
        starts=[],
        ends=[],
        route_tiles=[],
        choke_points=[],
        candidates=[],
    )
    candidate = _candidate()
    monkeypatch.setattr(
        "autopush.planner.rank_strategies",
        lambda *_args, **_kwargs: [candidate],
    )

    plan = build_round_plan(
        stage_id="act55side_ex05",
        stage_map=stage_map,
        strategy_paths=["candidate.json"],
        roster={},
        substitutions={},
        metrics=RoundMetrics(
            round_name="planner-fallback",
            stage="act55side_ex05",
        ),
        model_client=_FakeModel(exception=ModelError("model unavailable")),
    )

    assert plan["selected_strategy"]["title"] == "candidate"
    assert "model selection unavailable" in plan["strategy_selection"]


def test_job_without_requirements_is_never_reported_ready():
    strategy = CopilotStrategy.from_dict(
        {
            "stage_name": "act55side_ex05",
            "opers": [{"name": "结城理", "skill": 3}],
            "actions": [{"type": "SpeedUp"}],
        }
    )
    evaluation = evaluate_strategy(
        strategy,
        {
            "结城理": RosterOperator(
                name="结城理",
                elite=0,
                level=1,
                skill_level=0,
            )
        },
    )

    assert evaluation.status == "unverified"
    assert evaluation.runnable
    assert "requirements missing" in evaluation.diagnostics[0]


def test_failed_strategy_can_be_excluded_from_selection(
    monkeypatch,
):
    stage_map = StageMap(
        stage_id="act55side_ex05",
        stage_code="TL-EX-5",
        name="test",
        width=0,
        height=0,
        tiles=[],
        starts=[],
        ends=[],
        route_tiles=[],
        choke_points=[],
        candidates=[],
    )
    failed = _candidate()
    replacement = replace(
        _candidate(),
        strategy=type(
            "Strategy",
            (),
            {
                "strategy_id": "replacement.json",
                "title": "replacement",
                "action_count": 1,
            },
        )(),
    )
    monkeypatch.setattr(
        "autopush.planner.rank_strategies",
        lambda *_args, **_kwargs: [failed, replacement],
    )

    plan = build_round_plan(
        stage_id="act55side_ex05",
        stage_map=stage_map,
        strategy_paths=["candidate.json"],
        roster={},
        substitutions={},
        metrics=RoundMetrics(
            round_name="planner-exclusion",
            stage="act55side_ex05",
        ),
        excluded_strategy_ids=["candidate.json"],
    )

    assert plan["selected_strategy"]["title"] == "replacement"
    assert plan["excluded_strategies"]


def test_failed_strategy_fingerprint_excludes_equivalent_tactics(
    monkeypatch,
):
    stage_map = StageMap(
        stage_id="act55side_ex05",
        stage_code="TL-EX-5",
        name="test",
        width=0,
        height=0,
        tiles=[],
        starts=[],
        ends=[],
        route_tiles=[],
        choke_points=[],
        candidates=[],
    )
    raw = {
        "stage_name": "act55side_ex05",
        "opers": [{"name": "A"}],
        "actions": [{"type": "SpeedUp"}],
    }

    def candidate(path, raw_payload, title):
        return replace(
            _candidate(),
            strategy=CopilotStrategy.from_dict(
                raw_payload,
                path=Path(path),
            ),
        )

    failed = candidate("failed.json", raw, "failed")
    duplicate = candidate("duplicate.json", raw, "duplicate")
    replacement = candidate(
        "replacement.json",
        {
            **raw,
            "actions": [{"type": "SkillDaemon"}],
        },
        "replacement",
    )
    monkeypatch.setattr(
        "autopush.planner.rank_strategies",
        lambda *_args, **_kwargs: [failed, duplicate, replacement],
    )

    plan = build_round_plan(
        stage_id="act55side_ex05",
        stage_map=stage_map,
        strategy_paths=["failed.json"],
        roster={},
        substitutions={},
        metrics=RoundMetrics(
            round_name="planner-fingerprint",
            stage="act55side_ex05",
        ),
        excluded_strategy_ids=["failed.json"],
    )

    assert plan["selected_strategy"]["path"].endswith("replacement.json")
