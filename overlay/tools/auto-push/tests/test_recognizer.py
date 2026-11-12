import cv2
import numpy as np

from autopush.recognizer import FrameGate, difference_hash, hash_distance


def test_frame_gate_ignores_near_identical_frames():
    first = np.zeros((72, 128, 3), dtype=np.uint8)
    second = np.zeros((72, 128, 3), dtype=np.uint8)
    cv2.circle(second, (64, 36), 12, (0, 0, 255), -1)

    gate = FrameGate(minimum_hash_distance=2, minimum_interval_seconds=0)
    assert gate.should_process(first, now_seconds=0.0)
    assert not gate.should_process(first.copy(), now_seconds=1.0)
    assert gate.should_process(second, now_seconds=2.0)
    assert hash_distance(difference_hash(first), difference_hash(second)) > 0
