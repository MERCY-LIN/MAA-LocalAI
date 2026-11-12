"""Low-cost visual state memory for repeatable ADB navigation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .metrics import RoundMetrics
from .recognizer import difference_hash, hash_distance


@dataclass(frozen=True)
class StateReference:
    name: str
    path: Path
    image_hash: int
    color_histogram: np.ndarray
    grayscale_thumbnail: np.ndarray


@dataclass(frozen=True)
class StateMatch:
    name: str
    distance: int
    confidence: float
    known: bool
    reference_path: Path | None


def _color_histogram(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
    cv2.normalize(histogram, histogram)
    return histogram


def _grayscale_thumbnail(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)


class VisualStateMemory:
    """Recognize previously explored screens without OCR or model vision."""

    def __init__(
        self,
        references: list[StateReference],
        *,
        maximum_distance: int = 8,
        minimum_margin: int = 2,
        maximum_thumbnail_mae: float = 0.12,
    ):
        self.references = references
        self.maximum_distance = maximum_distance
        self.minimum_margin = minimum_margin
        self.maximum_thumbnail_mae = maximum_thumbnail_mae

    @classmethod
    def load(
        cls,
        directory: str | Path,
        *,
        maximum_distance: int = 8,
        minimum_margin: int = 2,
        metrics: RoundMetrics | None = None,
    ) -> VisualStateMemory:
        references: list[StateReference] = []
        for path in sorted(Path(directory).glob("*.png")):
            if metrics is not None:
                metrics.record_tool_call()
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                continue
            references.append(
                StateReference(
                    name=path.stem,
                    path=path.resolve(),
                    image_hash=difference_hash(image),
                    color_histogram=_color_histogram(image),
                    grayscale_thumbnail=_grayscale_thumbnail(image),
                )
            )
        if not references:
            raise ValueError("state memory contains no readable PNG references")
        return cls(
            references,
            maximum_distance=maximum_distance,
            minimum_margin=minimum_margin,
        )

    def recognize(
        self,
        image: np.ndarray,
        *,
        metrics: RoundMetrics | None = None,
    ) -> StateMatch:
        if metrics is not None:
            metrics.record_tool_call()
        image_hash = difference_hash(image)
        histogram = _color_histogram(image)
        thumbnail = _grayscale_thumbnail(image)
        ranked = sorted(
            (
                (
                    hash_distance(image_hash, reference.image_hash),
                    float(
                        np.mean(
                            np.abs(
                                thumbnail - reference.grayscale_thumbnail
                            )
                        )
                        / 255.0
                    ),
                    -float(
                        cv2.compareHist(
                            histogram,
                            reference.color_histogram,
                            cv2.HISTCMP_CORREL,
                        )
                    ),
                    reference,
                )
                for reference in self.references
            ),
            key=lambda item: (item[0], item[1], item[2]),
        )
        distance, thumbnail_mae, negative_histogram_correlation, best = ranked[0]
        second_distance = ranked[1][0] if len(ranked) > 1 else 64
        margin = second_distance - distance
        confidence = max(
            0.0,
            min(
                1.0,
                (1.0 - distance / 64.0) * 0.75
                + (1.0 - thumbnail_mae) * 0.125
                + (negative_histogram_correlation + 1.0) * 0.125,
            ),
        )
        known = (
            distance <= self.maximum_distance
            and thumbnail_mae <= self.maximum_thumbnail_mae
            and margin >= self.minimum_margin
        )
        return StateMatch(
            name=best.name if known else "unknown",
            distance=distance,
            confidence=round(confidence, 4),
            known=known,
            reference_path=best.path if known else None,
        )
