from pathlib import Path

import cv2
import numpy as np

from autopush.verifier import BattleResultVerifier


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


def test_blank_screen_requires_computer_use():
    verifier = BattleResultVerifier(TEMPLATE_ROOT)
    result = verifier.verify(np.zeros((720, 1280, 3), dtype=np.uint8))
    assert result.status == "unknown"
    assert result.needs_computer_use
