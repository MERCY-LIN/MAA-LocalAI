"""Round planner that combines local map rules and strategy compatibility."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from .map_planner import StageMap
from .metrics import RoundMetrics, TestResult
from .model_client import LocalModelClient, ModelError
from .strategy import (
    CandidateEvaluation,
    RosterOperator,
    rank_strategies,
    strategy_fingerprint,
)


def load_roster(path: str | Path, metrics: RoundMetrics | None = None) -> dict[str, RosterOperator]:
    if metrics is not None:
        metrics.record_tool_call()
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict) and isinstance(payload.get("details"), dict):
        payload = payload["details"]
    if isinstance(payload, dict) and isinstance(payload.get("own_opers"), list):
        items = payload["own_opers"]
    elif isinstance(payload, dict) and isinstance(payload.get("operators"), list):
        items = payload["operators"]
    else:
        items = payload
    roster: dict[str, RosterOperator] = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        if item.get("own") is False:
            continue
        main_skill_level = int(item.get("mainSkillLevel", 0) or 0)
        mastery_levels = [
            int(skill.get("level", 0) or 0)
            for skill in item.get("skills", [])
            if isinstance(skill, dict)
        ]
        skill_levels = tuple(
            main_skill_level + mastery for mastery in mastery_levels
        )
        explicit_skill_level = int(item.get("skill_level", 0) or 0)
        skill_level = (
            max(skill_levels)
            if skill_levels
            else explicit_skill_level or main_skill_level
        )
        equips = [
            int(equip.get("level", 0) or 0)
            for equip in item.get("equips", [])
            if isinstance(equip, dict)
        ]
        module_level = int(
            item.get("module_level", max(equips, default=-1))
            if item.get("module_level") is not None
            else max(equips, default=-1)
        )
        operator = RosterOperator(
            name=str(item["name"]),
            elite=int(item.get("elite", 0) or 0),
            level=int(item.get("level", 0) or 0),
            skill_level=skill_level,
            module_level=module_level,
            skill_levels=skill_levels,
        )
        roster[operator.name] = operator
    return roster


def load_substitutions(
    path: str | Path | None,
    metrics: RoundMetrics | None = None,
) -> dict[str, list[str]]:
    if path is None:
        return {}
    if metrics is not None:
        metrics.record_tool_call()
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("substitutions file must contain an object")
    return {
        str(required): [str(item) for item in replacements]
        for required, replacements in payload.items()
        if isinstance(replacements, list)
    }


def _extract_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            payload, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("model reply contains no JSON object")


def _compact_candidates(candidates: list[CandidateEvaluation]) -> str:
    lines: list[str] = []
    for index, candidate in enumerate(candidates[:12], start=1):
        missing = ",".join(candidate.missing) or "-"
        lines.append(
            f"{index}. status={candidate.status} score={candidate.score} "
            f"missing={missing} actions={candidate.strategy.action_count} "
            f"title={candidate.strategy.title}"
        )
    return "\n".join(lines)


def choose_strategy_with_model(
    client: LocalModelClient,
    candidates: list[CandidateEvaluation],
    *,
    stage_id: str,
    metrics: RoundMetrics,
) -> int | None:
    runnable = [item for item in candidates if item.runnable]
    if not runnable:
        return None
    system_prompt = """
You select one locally validated MAA Copilot strategy for a first-clear attempt.
The candidates and compatibility checks were produced by deterministic local rules.
Never invent account data, operator levels, stage facts, clicks, or coordinates.
Return only JSON:
{"candidate":1,"reason":"short Chinese reason","assumptions":["..."]}
Choose only from the numbered runnable candidates.
"""
    user_prompt = (
        f"Stage: {stage_id}\n"
        f"Runnable candidates:\n{_compact_candidates(runnable)}\n"
    )
    reply = client.complete_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        metrics=metrics,
        max_tokens=350,
    )
    payload = _extract_json_object(reply.content)
    number = int(payload.get("candidate", 0))
    if not 1 <= number <= len(runnable):
        raise ValueError("model selected a candidate outside the whitelist")
    return number - 1


def build_round_plan(
    *,
    stage_id: str,
    stage_map: StageMap,
    strategy_paths: Iterable[str | Path],
    roster: dict[str, RosterOperator],
    substitutions: dict[str, list[str]],
    metrics: RoundMetrics,
    model_client: LocalModelClient | None = None,
    allow_support: bool = True,
    excluded_strategy_ids: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Create an auditable plan without touching the emulator."""

    candidates = rank_strategies(
        strategy_paths,
        stage_id,
        roster,
        substitutions=substitutions,
        allow_support=allow_support,
        metrics=metrics,
    )
    excluded_raw = {
        str(strategy_id).strip()
        for strategy_id in excluded_strategy_ids
        if str(strategy_id).strip()
    }
    excluded = {
        str(Path(strategy_id).expanduser().resolve())
        for strategy_id in excluded_raw
    }
    if excluded:
        excluded_fingerprints = {
            strategy_fingerprint(item.strategy.raw)
            for item in candidates
            if item.strategy.strategy_id in excluded_raw
            or str(Path(item.strategy.strategy_id).resolve()) in excluded
            if isinstance(getattr(item.strategy, "raw", None), dict)
        }
        candidates = [
            item
            for item in candidates
            if item.strategy.strategy_id not in excluded_raw
            and str(Path(item.strategy.strategy_id).resolve()) not in excluded
            and (
                not isinstance(getattr(item.strategy, "raw", None), dict)
                or strategy_fingerprint(item.strategy.raw)
                not in excluded_fingerprints
            )
        ]
    metrics.add_test(
        TestResult(
            name="map-analysis",
            success=bool(stage_map.candidates and stage_map.route_tiles),
            detail=(
                f"routes={len(stage_map.route_tiles)} "
                f"chokes={len(stage_map.choke_points)} "
                f"candidates={len(stage_map.candidates)}"
            ),
        )
    )
    metrics.add_test(
        TestResult(
            name="strategy-ranking",
            success=any(item.runnable for item in candidates),
            detail=(
                f"total={len(candidates)} "
                f"runnable={sum(item.runnable for item in candidates)}"
            ),
        )
    )

    selected_index = 0 if any(item.runnable for item in candidates) else None
    selection_reason = "local ranking"
    if model_client is not None and selected_index is not None:
        try:
            selected_index = choose_strategy_with_model(
                model_client,
                candidates,
                stage_id=stage_id,
                metrics=metrics,
            )
        except (ModelError, ValueError, TypeError, KeyError) as exception:
            selection_reason = (
                "local ranking; model selection unavailable: "
                f"{type(exception).__name__}: {exception}"
            )
            metrics.add_note(selection_reason)
        else:
            selection_reason = "local model selected from validated candidates"

    selected = candidates[selected_index] if selected_index is not None else None
    plan = {
        "stage_id": stage_id,
        "stage_code": stage_map.stage_code,
        "stage_name": stage_map.name,
        "strategy_selection": selection_reason,
        "excluded_strategies": sorted(excluded),
        "selected_strategy": (
            {
                "path": selected.strategy.strategy_id,
                "title": selected.strategy.title,
                "status": selected.status,
                "score": selected.score,
                "missing": selected.missing,
                "substitutions": selected.substitutions,
                "support_needed": selected.support_needed,
                "diagnostics": selected.diagnostics,
                "action_count": selected.strategy.action_count,
            }
            if selected is not None
            else None
        ),
        "map": {
            "starts": stage_map.starts,
            "ends": stage_map.ends,
            "choke_points": stage_map.choke_points,
            "top_candidates": [
                asdict(candidate) for candidate in stage_map.candidates[:16]
            ],
        },
        "ranked_strategies": [
            {
                "path": item.strategy.strategy_id,
                "title": item.strategy.title,
                "status": item.status,
                "score": item.score,
                "missing": item.missing,
                "substitutions": item.substitutions,
                "support_needed": item.support_needed,
            }
            for item in candidates
        ],
        "execution": {
            "mode": "dry-run",
            "reason": "This milestone produces and validates the opening plan only.",
            "trial_policy": {
                "use_drill_first": False,
                "max_attempts": 6,
                "max_unknown_verifications": 2,
                "max_computer_use_operations": metrics.limits.computer_use_operations,
                "require_three_star_before_real_run": False,
                "require_three_star_after_real_run": True,
            },
        },
    }
    metrics.add_test(
        TestResult(
            name="plan-selection",
            success=selected is not None and selected.runnable,
            detail=selection_reason,
            strategy_path=selected.strategy.strategy_id if selected else None,
        )
    )
    return plan
