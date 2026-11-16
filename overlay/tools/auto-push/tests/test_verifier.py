from pathlib import Path

import cv2
import numpy as np

from autopush.verifier import (
    BattleResultVerifier,
    ResultVerification,
    resolve_battle_evidence,
)


REPOSITORY = Path(__file__).resolve().parents[3]
TEMPLATE_ROOT = REPOSITORY / "resource" / "template" / "Battle"


def test_three_star_template_is_verified_locally():
    verifier = BattleResultVerifier(TEMPLATE_ROOT)
    stars = cv2.imread(
        str(TEMPLATE_ROOT / "StageDrops" / "StageDrops-Stars-3.png")
    )
    end = cv2.imread(str(TEMPLATE_ROOT / "BattleFlag" / "EndOfAction.png"))
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[100:100 + end.shape[0], 100:100 + end.shape[1]] = end
    image[500:500 + stars.shape[0], 300:300 + stars.shape[1]] = stars

    result = verifier.verify(image)
    assert result.status == "three_star"
    assert result.stars == 3
    assert not result.needs_computer_use


def _verification(status: str, stars: int | None) -> ResultVerification:
    return ResultVerification(
        status=status,
        stars=stars,
        confidence=0.95,
        evidence=(),
        needs_computer_use=status == "unknown",
    )


def test_core_three_star_never_overrides_a_two_star_screenshot():
    resolution = resolve_battle_evidence(
        _verification("two_star", 2),
        core_three_star=True,
        core_evidence=[{"task": "StageDrops-Stars-3", "score": 0.99}],
    )

    assert resolution.status == "conflict"
    assert resolution.conflict
    assert resolution.stars is None
    assert set(resolution.sources) == {"screenshot", "maa_core"}


def test_core_three_star_without_a_settlement_screenshot_is_unverified():
    resolution = resolve_battle_evidence(
        None,
        core_three_star=True,
        core_evidence=[{"task": "StageDrops-Stars-3", "score": 0.99}],
    )

    assert resolution.status == "conflict"
    assert resolution.conflict
    assert resolution.stars is None


def test_agreement_between_screenshot_and_core_is_three_star():
    resolution = resolve_battle_evidence(
        _verification("three_star", 3),
        core_three_star=True,
        core_evidence=[{"task": "StageDrops-Stars-3", "score": 0.99}],
    )

    assert resolution.status == "three_star"
    assert not resolution.conflict
    assert resolution.stars == 3


def test_blank_screen_requires_computer_use():
    verifier = BattleResultVerifier(TEMPLATE_ROOT)
    result = verifier.verify(np.zeros((720, 1280, 3), dtype=np.uint8))
    assert result.status == "unknown"
    assert result.needs_computer_use
