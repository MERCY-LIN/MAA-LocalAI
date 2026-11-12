from pathlib import Path

import cv2
import numpy as np

from autopush.state_memory import VisualStateMemory


def test_visual_state_memory_recognizes_known_layout(tmp_path: Path):
    first = np.zeros((180, 320, 3), dtype=np.uint8)
    cv2.rectangle(first, (10, 10), (150, 80), (20, 120, 220), -1)
    second = np.zeros((180, 320, 3), dtype=np.uint8)
    cv2.circle(second, (220, 100), 55, (40, 220, 80), -1)
    cv2.imwrite(str(tmp_path / "home.png"), first)
    cv2.imwrite(str(tmp_path / "battle.png"), second)

    memory = VisualStateMemory.load(tmp_path)
    assert memory.recognize(first).name == "home"
    assert memory.recognize(second).name == "battle"
    assert memory.recognize(np.full_like(first, 255)).name == "unknown"
