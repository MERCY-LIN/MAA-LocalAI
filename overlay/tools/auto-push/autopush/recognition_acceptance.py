"""External, human-confirmed recognition acceptance corpus support."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import cv2

from .maa_core import MaaEvent, summarize_events
from .verifier import (
    BattleResultVerifier,
    ResultVerification,
    resolve_battle_evidence,
)


CORPUS_SCHEMA = "localai.recognition-corpus/v1"
VISUAL_LABELS = {"three_star", "two_star", "non_three_star", "unknown"}


class RecognitionCorpusError(ValueError):
    """Raised when an acceptance corpus is not safely reviewable."""


@dataclass(frozen=True)
class CorpusCaseResult:
    case_id: str
    image: str
    expected_visual: str
    actual_visual: str
    visual_conflict: bool
    visual_false_positive: bool
    visual_false_negative: bool
    expected_callback_three_star: bool | None
    actual_callback_three_star: bool | None
    callback_mismatch: bool
    evidence_status: str
    evidence_conflict: bool


@dataclass(frozen=True)
class CorpusReport:
    schema_version: str
    corpus: str
    reviewed_by: str
    reviewed_at: str
    cases: tuple[CorpusCaseResult, ...]
    false_positives: int
    false_negatives: int
    visual_conflicts: int
    callback_mismatches: int
    evidence_conflicts: int
    passed: bool


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exception:
        raise RecognitionCorpusError(
            f"cannot read recognition corpus manifest: {exception}"
        ) from exception
    if not isinstance(payload, dict):
        raise RecognitionCorpusError("recognition corpus manifest must be an object")
    if payload.get("schema_version") != CORPUS_SCHEMA:
        raise RecognitionCorpusError(
            f"unsupported recognition corpus schema: "
            f"{payload.get('schema_version')!r}"
        )
    return payload


def _require_text(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise RecognitionCorpusError(f"{name} is required")
    return value.strip()


def run_recognition_corpus(
    manifest_path: str | Path,
    *,
    template_root: str | Path,
    threshold: float = 0.86,
    verifier_factory: Callable[..., BattleResultVerifier] = BattleResultVerifier,
) -> CorpusReport:
    """Evaluate real screenshots and their captured callbacks.

    Images and callback captures remain outside source control. Each case must
    identify a real screenshot and a human-reviewed expected result.
    """

    path = Path(manifest_path).expanduser().resolve()
    root = path.parent
    manifest = _read_manifest(path)
    reviewed_by = _require_text(manifest, "reviewed_by")
    reviewed_at = _require_text(manifest, "reviewed_at")
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise RecognitionCorpusError("recognition corpus requires cases")

    verifier = verifier_factory(template_root, threshold=threshold)
    results: list[CorpusCaseResult] = []
    seen_ids: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise RecognitionCorpusError("each corpus case must be an object")
        case_id = _require_text(raw_case, "id")
        if case_id in seen_ids:
            raise RecognitionCorpusError(f"duplicate corpus case id: {case_id}")
        seen_ids.add(case_id)
        if raw_case.get("source") != "real_screenshot":
            raise RecognitionCorpusError(
                f"{case_id}: source must be real_screenshot"
            )
        if raw_case.get("human_confirmed") is not True:
            raise RecognitionCorpusError(
                f"{case_id}: human_confirmed must be true"
            )
        expected_visual = _require_text(raw_case, "expected_visual")
        if expected_visual not in VISUAL_LABELS:
            raise RecognitionCorpusError(
                f"{case_id}: unsupported expected_visual {expected_visual!r}"
            )
        image_path = (root / _require_text(raw_case, "image")).resolve()
        try:
            image_path.relative_to(root)
        except ValueError as exception:
            raise RecognitionCorpusError(
                f"{case_id}: image must stay inside the corpus directory"
            ) from exception
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise RecognitionCorpusError(
                f"{case_id}: unable to read screenshot {image_path}"
            )
        verification = verifier.verify(image)

        callback_cases = raw_case.get("callbacks", [])
        if not isinstance(callback_cases, list):
            raise RecognitionCorpusError(
                f"{case_id}: callbacks must be a list"
            )
        callback_summary = summarize_events(
            [
                MaaEvent(
                    message_id=int(item.get("message_id", 0)),
                    details=dict(item.get("details") or {}),
                )
                for item in callback_cases
                if isinstance(item, dict)
            ]
        )
        expected_callback = raw_case.get("expected_callback_three_star")
        if expected_callback is not None and not isinstance(expected_callback, bool):
            raise RecognitionCorpusError(
                f"{case_id}: expected_callback_three_star must be boolean"
            )
        actual_callback = (
            bool(callback_summary["maa_three_star"])
            if callback_cases
            else None
        )
        resolution = resolve_battle_evidence(
            verification,
            core_three_star=bool(actual_callback),
            core_evidence=callback_summary["maa_three_star_evidence"],
        )
        visual_conflict = verification.status != expected_visual
        results.append(
            CorpusCaseResult(
                case_id=case_id,
                image=str(image_path),
                expected_visual=expected_visual,
                actual_visual=verification.status,
                visual_conflict=visual_conflict,
                visual_false_positive=(
                    verification.status == "three_star"
                    and expected_visual != "three_star"
                ),
                visual_false_negative=(
                    expected_visual == "three_star"
                    and verification.status != "three_star"
                ),
                expected_callback_three_star=expected_callback,
                actual_callback_three_star=actual_callback,
                callback_mismatch=(
                    expected_callback is not None
                    and actual_callback != expected_callback
                ),
                evidence_status=resolution.status,
                evidence_conflict=resolution.conflict,
            )
        )

    false_positives = sum(item.visual_false_positive for item in results)
    false_negatives = sum(item.visual_false_negative for item in results)
    visual_conflicts = sum(item.visual_conflict for item in results)
    callback_mismatches = sum(item.callback_mismatch for item in results)
    evidence_conflicts = sum(item.evidence_conflict for item in results)
    return CorpusReport(
        schema_version=CORPUS_SCHEMA,
        corpus=str(path),
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        cases=tuple(results),
        false_positives=false_positives,
        false_negatives=false_negatives,
        visual_conflicts=visual_conflicts,
        callback_mismatches=callback_mismatches,
        evidence_conflicts=evidence_conflicts,
        passed=all(
            not item.visual_conflict
            and not item.callback_mismatch
            and not item.evidence_conflict
            for item in results
        ),
    )


def report_payload(report: CorpusReport) -> dict[str, Any]:
    return asdict(report)
