"""OpenCV state-change detection and local template matching."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

from .metrics import RoundMetrics

cv2.setNumThreads(2)


def decode_png(payload: bytes) -> np.ndarray:
    """Decode image bytes into a BGR OpenCV image."""

    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("failed to decode screenshot")
    return image


def difference_hash(image: np.ndarray) -> int:
    """Return a compact 64-bit dHash used to skip redundant model/OCR work."""

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = small[:, 1:] > small[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bool(bit))
    return value


def hash_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


@dataclass
class FrameGate:
    """Sample state changes, not every game frame."""

    minimum_hash_distance: int = 4
    minimum_interval_seconds: float = 0.75
    previous_hash: int | None = None
    previous_time: float | None = None

    def should_process(
        self,
        image: np.ndarray,
        *,
        now_seconds: float,
    ) -> bool:
        current_hash = difference_hash(image)
        time_ready = (
            self.previous_time is None
            or now_seconds - self.previous_time >= self.minimum_interval_seconds
        )
        changed = (
            self.previous_hash is None
            or hash_distance(current_hash, self.previous_hash)
            >= self.minimum_hash_distance
        )
        self.previous_hash = current_hash
        if time_ready and changed:
            self.previous_time = now_seconds
            return True
        return False


@dataclass(frozen=True)
class TemplateMatch:
    name: str
    score: float
    x: int
    y: int
    width: int
    height: int


class TemplateLibrary:
    """Match MAA/local templates without sending frames to a model."""

    def __init__(self, templates: dict[str, np.ndarray]):
        self._templates = templates

    @classmethod
    def load(cls, paths: Iterable[str | Path]) -> TemplateLibrary:
        loaded: dict[str, np.ndarray] = {}
        for value in paths:
            path = Path(value)
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is not None:
                loaded[path.stem] = image
        return cls(loaded)

    def match(
        self,
        image: np.ndarray,
        *,
        threshold: float = 0.88,
        metrics: RoundMetrics | None = None,
    ) -> list[TemplateMatch]:
        if metrics is not None:
            metrics.record_tool_call()
        matches: list[TemplateMatch] = []
        for name, template in self._templates.items():
            if (
                template.shape[0] > image.shape[0]
                or template.shape[1] > image.shape[1]
            ):
                continue
            result = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
            _, maximum, _, maximum_location = cv2.minMaxLoc(result)
            if maximum < threshold:
                continue
            matches.append(
                TemplateMatch(
                    name=name,
                    score=round(float(maximum), 4),
                    x=int(maximum_location[0]),
                    y=int(maximum_location[1]),
                    width=int(template.shape[1]),
                    height=int(template.shape[0]),
                )
            )
        matches.sort(key=lambda item: -item.score)
        return matches
