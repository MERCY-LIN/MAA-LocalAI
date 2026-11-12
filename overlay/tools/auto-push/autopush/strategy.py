"""Local Copilot strategy indexing and roster compatibility scoring."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .metrics import RoundMetrics


@dataclass(frozen=True)
class OperatorRequirement:
    """Minimum account state required by a Copilot operator entry."""

    name: str
    elite: int = 0
    level: int = 0
    skill_level: int = 0
    module_level: int = -1
    skill: int | None = None


@dataclass(frozen=True)
class RosterOperator:
    """Locally known operator state."""

    name: str
    elite: int
    level: int
    skill_level: int
    module_level: int = -1
    skill_levels: tuple[int, ...] = ()

    def effective_skill_level(self, skill: int | None) -> int:
        if skill is not None and 1 <= skill <= len(self.skill_levels):
            return self.skill_levels[skill - 1]
        return self.skill_level


@dataclass
class CopilotStrategy:
    """A locally stored Copilot JSON action plan."""

    path: Path | None
    stage_id: str
    title: str
    direct_operators: list[OperatorRequirement] = field(default_factory=list)
    operator_groups: list[list[OperatorRequirement]] = field(default_factory=list)
    action_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def strategy_id(self) -> str:
        if self.path is not None:
            return str(self.path.resolve())
        return f"{self.stage_id}:{self.title}"

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        path: Path | None = None,
    ) -> CopilotStrategy:
        direct = [
            _parse_requirement(item)
            for item in payload.get("opers", [])
            if isinstance(item, dict) and item.get("name")
        ]
        groups = [
            [
                _parse_requirement(operator)
                for operator in group.get("opers", [])
                if isinstance(operator, dict) and operator.get("name")
            ]
            for group in payload.get("groups", [])
            if isinstance(group, dict)
        ]
        title = str(payload.get("doc", {}).get("title") or payload.get("stage_name"))
        return cls(
            path=path,
            stage_id=str(payload.get("stage_name", "")),
            title=title,
            direct_operators=direct,
            operator_groups=[group for group in groups if group],
            action_count=len(payload.get("actions", [])),
            raw=payload,
        )

    @classmethod
    def load(cls, path: str | Path) -> CopilotStrategy:
        strategy_path = Path(path)
        with strategy_path.open("r", encoding="utf-8") as handle:
            wrapper = json.load(handle)
        payload = wrapper
        if isinstance(wrapper, dict) and isinstance(wrapper.get("content"), str):
            payload = json.loads(wrapper["content"])
        return cls.from_dict(payload, path=strategy_path)


@dataclass(frozen=True)
class CandidateEvaluation:
    """Compatibility result for one local strategy."""

    strategy: CopilotStrategy
    status: str
    score: float
    missing: tuple[str, ...]
    substitutions: tuple[tuple[str, str], ...]
    support_needed: bool
    diagnostics: tuple[str, ...]

    @property
    def runnable(self) -> bool:
        return self.status != "unavailable"


def _parse_requirement(payload: dict[str, Any]) -> OperatorRequirement:
    requirements = payload.get("requirements") or {}
    return OperatorRequirement(
        name=str(payload.get("name", "")),
        elite=int(requirements.get("elite", 0) or 0),
        level=int(requirements.get("level", 0) or 0),
        skill_level=int(requirements.get("skill_level", 0) or 0),
        module_level=int(requirements.get("module", -1) or -1),
        skill=int(payload["skill"]) if payload.get("skill") is not None else None,
    )


def _requirement_gap(
    requirement: OperatorRequirement,
    operator: RosterOperator,
) -> list[str]:
    gaps: list[str] = []
    if operator.elite < requirement.elite:
        gaps.append(
            f"{requirement.name}: elite {operator.elite} < {requirement.elite}"
        )
    if operator.level < requirement.level:
        gaps.append(f"{requirement.name}: level {operator.level} < {requirement.level}")
    effective_skill_level = operator.effective_skill_level(requirement.skill)
    if effective_skill_level < requirement.skill_level:
        gaps.append(
            f"{requirement.name}: skill {effective_skill_level} < "
            f"{requirement.skill_level}"
        )
    if (
        requirement.module_level >= 0
        and operator.module_level < requirement.module_level
    ):
        gaps.append(
            f"{requirement.name}: module {operator.module_level} < "
            f"{requirement.module_level}"
        )
    return gaps


def _best_group_option(
    group: list[OperatorRequirement],
    roster: dict[str, RosterOperator],
) -> tuple[OperatorRequirement | None, RosterOperator | None, list[str]]:
    scored: list[
        tuple[int, OperatorRequirement, RosterOperator, list[str]]
    ] = []
    for requirement in group:
        operator = roster.get(requirement.name)
        if operator is None:
            continue
        gaps = _requirement_gap(requirement, operator)
        scored.append((len(gaps), requirement, operator, gaps))
    if not scored:
        return None, None, [f"none available: {', '.join(op.name for op in group)}"]
    _, requirement, operator, gaps = min(scored, key=lambda item: item[0])
    return requirement, operator, gaps


def evaluate_strategy(
    strategy: CopilotStrategy,
    roster: dict[str, RosterOperator],
    *,
    substitutions: dict[str, list[str]] | None = None,
    allow_support: bool = True,
) -> CandidateEvaluation:
    """Evaluate without trusting model guesses about the account."""

    substitutions = substitutions or {}
    missing: list[str] = []
    diagnostics: list[str] = []
    chosen_substitutions: list[tuple[str, str]] = []
    degraded = 0

    for requirement in strategy.direct_operators:
        operator = roster.get(requirement.name)
        if operator is None:
            replacement = next(
                (
                    name
                    for name in substitutions.get(requirement.name, [])
                    if name in roster
                ),
                None,
            )
            if replacement is None:
                missing.append(requirement.name)
            else:
                chosen_substitutions.append((requirement.name, replacement))
        else:
            gaps = _requirement_gap(requirement, operator)
            if gaps:
                degraded += len(gaps)
                diagnostics.extend(gaps)

    for group in strategy.operator_groups:
        _, _, gaps = _best_group_option(group, roster)
        if gaps:
            diagnostics.extend(gaps)
            if gaps[0].startswith("none available"):
                missing.append("/".join(operator.name for operator in group))
            else:
                degraded += len(gaps)

    if len(missing) > 1:
        status = "unavailable"
    elif chosen_substitutions:
        status = "substitution"
    elif missing:
        status = "support" if allow_support else "unavailable"
    elif degraded:
        status = "degraded"
    else:
        status = "ready"

    status_bonus = {
        "ready": 100.0,
        "degraded": 75.0,
        "substitution": 60.0,
        "support": 35.0,
        "unavailable": 0.0,
    }[status]
    score = status_bonus - degraded * 3.0 - len(missing) * 25.0
    score += min(10.0, max(0.0, 20.0 - strategy.action_count) * 0.25)
    return CandidateEvaluation(
        strategy=strategy,
        status=status,
        score=round(score, 3),
        missing=tuple(missing),
        substitutions=tuple(chosen_substitutions),
        support_needed=status == "support",
        diagnostics=tuple(diagnostics),
    )


def collect_strategy_files(paths: Iterable[str | Path]) -> list[Path]:
    """Collect JSON strategy files from files or directory trees."""

    result: list[Path] = []
    for value in paths:
        path = Path(value)
        if path.is_file() and path.suffix.lower() == ".json":
            result.append(path)
        elif path.is_dir():
            for root, directories, files in os.walk(path, followlinks=False):
                directories[:] = [
                    name
                    for name in directories
                    if not (
                        (Path(root) / name).is_symlink()
                        or (Path(root) / name).is_junction()
                    )
                ]
                result.extend(
                    Path(root) / name
                    for name in files
                    if Path(name).suffix.lower() == ".json"
                )
    return sorted(set(path.resolve() for path in result))


def rank_strategies(
    paths: Iterable[str | Path],
    stage_id: str,
    roster: dict[str, RosterOperator],
    *,
    substitutions: dict[str, list[str]] | None = None,
    allow_support: bool = True,
    metrics: RoundMetrics | None = None,
) -> list[CandidateEvaluation]:
    """Load local strategy files and return best-first evaluations."""

    evaluations: list[CandidateEvaluation] = []
    for path in collect_strategy_files(paths):
        if metrics is not None:
            metrics.record_tool_call()
        try:
            strategy = CopilotStrategy.load(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if strategy.stage_id != stage_id:
            continue
        evaluations.append(
            evaluate_strategy(
                strategy,
                roster,
                substitutions=substitutions,
                allow_support=allow_support,
            )
        )

    evaluations.sort(
        key=lambda item: (
            not item.runnable,
            -item.score,
            len(item.missing),
            item.strategy.action_count,
            item.strategy.strategy_id,
        )
    )
    if metrics is not None:
        metrics.add_note(
            f"strategies stage={stage_id} total={len(evaluations)} "
            f"runnable={sum(item.runnable for item in evaluations)}"
        )
    return evaluations
