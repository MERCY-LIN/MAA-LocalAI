"""Local result verification using MAA result templates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .metrics import RoundMetrics

DEFAULT_TEMPLATE_ROOT = Path("resource") / "template" / "Battle"


@dataclass(frozen=True)
class TemplateEvidence:
    name: str
    score: float
    x: int
    y: int


@dataclass(frozen=True)
class ResultVerification:
    status: str
    stars: int | None
    confidence: float
    evidence: tuple[TemplateEvidence, ...]
    needs_computer_use: bool


@dataclass(frozen=True)
class EvidenceResolution:
    """Reconcile screenshot and MaaCore evidence without promoting conflicts."""

    status: str
    stars: int | None
    confidence: float
    conflict: bool
    reason: str
    sources: tuple[str, ...]


def resolve_battle_evidence(
    visual: ResultVerification | None,
    *,
    core_three_star: bool,
    core_evidence: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> EvidenceResolution:
    """Choose a conservative result while retaining both evidence sources."""

    visual_status = visual.status if visual is not None else "unknown"
    visual_confidence = visual.confidence if visual is not None else 0.0
    sources: list[str] = []
    if visual is not None:
        sources.append("screenshot")
    if core_evidence:
        sources.append("maa_core")

    if visual_status in {"three_star", "two_star", "non_three_star"}:
        visual_three_star = visual_status == "three_star"
        if core_three_star and not visual_three_star:
            return EvidenceResolution(
                status="conflict",
                stars=None,
                confidence=visual_confidence,
                conflict=True,
                reason="screenshot disagrees with MaaCore three-star callback",
                sources=tuple(sources),
            )
        if visual_three_star and core_three_star:
            return EvidenceResolution(
                status="three_star",
                stars=3,
                confidence=visual_confidence,
                conflict=False,
                reason="screenshot and MaaCore both report three stars",
                sources=tuple(sources),
            )
        return EvidenceResolution(
            status=visual_status,
            stars=visual.stars if visual is not None else None,
            confidence=visual_confidence,
            conflict=False,
            reason=(
                "screenshot provides an explicit result"
                if not core_three_star
                else "unreachable"
            ),
            sources=tuple(sources),
        )

    if core_three_star:
        return EvidenceResolution(
            status="conflict",
            stars=None,
            confidence=visual_confidence,
            conflict=True,
            reason=(
                "MaaCore reports three stars but the settlement screenshot is "
                "missing or unknown"
            ),
            sources=tuple(sources),
        )
    return EvidenceResolution(
        status="unknown",
        stars=None,
        confidence=visual_confidence,
        conflict=False,
        reason="no explicit settlement result was verified",
        sources=tuple(sources),
    )


class BattleResultVerifier:
    """Distinguish three-star, non-three-star, and unknown result screens."""

    def __init__(
        self,
        template_root: str | Path = DEFAULT_TEMPLATE_ROOT,
        *,
        threshold: float = 0.86,
    ):
        root = Path(template_root)
        self.threshold = threshold
        self.templates = {
            name: self._load(root, relative)
            for name, relative in {
                "end_of_action": Path("BattleFlag") / "EndOfAction.png",
                "stars_3": Path("StageDrops") / "StageDrops-Stars-3.png",
                "stars_2": Path("StageDrops") / "StageDrops-Stars-2.png",
                "stars_adverse": Path("StageDrops") / "StageDrops-Stars-Adverse.png",
            }.items()
        }

    @staticmethod
    def _load(root: Path, relative: Path) -> np.ndarray:
        path = root / relative
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise FileNotFoundError(path)
        return image

    def _match(
        self,
        image: np.ndarray,
        template: np.ndarray,
    ) -> TemplateEvidence | None:
        if (
            template.shape[0] > image.shape[0]
            or template.shape[1] > image.shape[1]
        ):
            return None
        result = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
        _, maximum, _, location = cv2.minMaxLoc(result)
        if maximum < self.threshold:
            return None
        return TemplateEvidence(
            name="",
            score=round(float(maximum), 4),
            x=int(location[0]),
            y=int(location[1]),
        )

    def verify(
        self,
        image: np.ndarray,
        *,
        metrics: RoundMetrics | None = None,
    ) -> ResultVerification:
        if metrics is not None:
            metrics.record_tool_call()
        evidence: list[TemplateEvidence] = []
        matches: dict[str, TemplateEvidence | None] = {}
        for name, template in self.templates.items():
            match = self._match(image, template)
            matches[name] = match
            if match is not None:
                evidence.append(
                    TemplateEvidence(
                        name=name,
                        score=match.score,
                        x=match.x,
                        y=match.y,
                    )
                )

        stars_3 = matches["stars_3"]
        stars_2 = matches["stars_2"]
        adverse = matches["stars_adverse"]
        end_of_action = matches["end_of_action"]
        best = max((item.score for item in evidence), default=0.0)

        if stars_3 is not None or adverse is not None:
            return ResultVerification(
                status="three_star",
                stars=3,
                confidence=best,
                evidence=tuple(evidence),
                needs_computer_use=False,
            )
        if stars_2 is not None:
            return ResultVerification(
                status="two_star",
                stars=2,
                confidence=best,
                evidence=tuple(evidence),
                needs_computer_use=False,
            )
        if end_of_action is not None:
            return ResultVerification(
                status="non_three_star",
                stars=None,
                confidence=best,
                evidence=tuple(evidence),
                needs_computer_use=False,
            )
        return ResultVerification(
            status="unknown",
            stars=None,
            confidence=best,
            evidence=tuple(evidence),
            needs_computer_use=True,
        )
