import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from autopush.recognition_acceptance import (
    CORPUS_SCHEMA,
    RecognitionCorpusError,
    run_recognition_corpus,
)
from autopush.verifier import ResultVerification


class _FakeVerifier:
    def __init__(self, _root, *, threshold):
        self.threshold = threshold

    def verify(self, _image):
        return ResultVerification(
            status="two_star",
            stars=2,
            confidence=0.95,
            evidence=(),
            needs_computer_use=False,
        )


def _write_manifest(root: Path, case: dict) -> Path:
    path = root / "corpus.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": CORPUS_SCHEMA,
                "reviewed_by": "human-reviewer",
                "reviewed_at": "2026-11-16T12:00:00+08:00",
                "cases": [case],
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_image(root: Path) -> None:
    assert cv2.imwrite(
        str(root / "capture.png"),
        np.zeros((720, 1280, 3), dtype=np.uint8),
    )


def test_acceptance_corpus_rejects_template_composites(tmp_path: Path):
    manifest = _write_manifest(
        tmp_path,
        {
            "id": "template-copy",
            "image": "capture.png",
            "source": "template_composite",
            "human_confirmed": True,
            "expected_visual": "three_star",
        },
    )

    with pytest.raises(RecognitionCorpusError, match="real_screenshot"):
        run_recognition_corpus(
            manifest,
            template_root=tmp_path,
            verifier_factory=_FakeVerifier,
        )


def test_acceptance_corpus_requires_human_confirmation(tmp_path: Path):
    manifest = _write_manifest(
        tmp_path,
        {
            "id": "unreviewed",
            "image": "capture.png",
            "source": "real_screenshot",
            "human_confirmed": False,
            "expected_visual": "three_star",
        },
    )

    with pytest.raises(RecognitionCorpusError, match="human_confirmed"):
        run_recognition_corpus(
            manifest,
            template_root=tmp_path,
            verifier_factory=_FakeVerifier,
        )


def test_acceptance_corpus_counts_visual_and_callback_conflicts(tmp_path: Path):
    _write_image(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        {
            "id": "conflicting-evidence",
            "image": "capture.png",
            "source": "real_screenshot",
            "human_confirmed": True,
            "expected_visual": "two_star",
            "expected_callback_three_star": True,
            "callbacks": [
                {
                    "message_id": 10001,
                    "details": {"taskchain": "Copilot", "taskid": 9},
                },
                {
                    "message_id": 20001,
                    "details": {
                        "taskchain": "Copilot",
                        "taskid": 9,
                        "details": {
                            "task": "Copilot@StageDrops-Stars-3",
                            "result": {"score": 0.99},
                        },
                    },
                },
                {
                    "message_id": 10002,
                    "details": {"taskchain": "Copilot", "taskid": 9},
                },
            ],
        },
    )

    report = run_recognition_corpus(
        manifest,
        template_root=tmp_path,
        threshold=0.91,
        verifier_factory=_FakeVerifier,
    )

    assert report.passed is False
    assert report.false_positives == 0
    assert report.false_negatives == 0
    assert report.visual_conflicts == 0
    assert report.callback_mismatches == 0
    assert report.evidence_conflicts == 1
    assert report.cases[0].evidence_status == "conflict"
