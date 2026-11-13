import json
import sys
import types
from pathlib import Path

import cv2
import numpy as np

import autopush.debug_cli as debug_cli
from autopush.adb import AdbError
from autopush.debug_cli import (
    _default_adb,
    _capture_verified_result,
    _is_safe_preflight_state,
    _snapshot_image,
    main,
)
from autopush.evidence import EvidenceError
from autopush.metrics import RoundMetrics
from autopush.verifier import ResultVerification
from autopush.evidence import RunStore
from autopush.states import RunPhase


REPOSITORY = Path(__file__).resolve().parents[3]
TEMPLATE_ROOT = REPOSITORY / "resource" / "template" / "Battle"


def _three_star_image() -> np.ndarray:
    stars = cv2.imread(
        str(TEMPLATE_ROOT / "StageDrops" / "StageDrops-Stars-3.png")
    )
    end = cv2.imread(str(TEMPLATE_ROOT / "BattleFlag" / "EndOfAction.png"))
    assert stars is not None and end is not None
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[100:100 + end.shape[0], 100:100 + end.shape[1]] = end
    image[500:500 + stars.shape[0], 300:300 + stars.shape[1]] = stars
    return image


def test_replay_reuses_saved_result_image(
    tmp_path: Path,
    capsys,
):
    evidence_root = tmp_path / "evidence"
    store = RunStore(
        evidence_root,
        run_id="run-replay",
        attempt_id="attempt-replay",
    )
    store.update_status(RunPhase.SUCCEEDED, stage="stage")
    store.append_event(
        "maa_callback",
        {
            "message_id": 10001,
            "details": {"taskchain": "Copilot", "taskid": 7},
        },
    )
    store.append_event(
        "maa_callback",
        {
            "message_id": 10002,
            "details": {"taskchain": "Copilot", "taskid": 7},
        },
    )
    result_path = store.run_dir / "artifacts" / "result.png"
    result_path.parent.mkdir(parents=True)
    assert cv2.imwrite(str(result_path), _three_star_image())

    exit_code = main(
        [
            "replay",
            "--evidence-root",
            str(evidence_root),
            "--run-id",
            "run-replay",
            "--template-root",
            str(TEMPLATE_ROOT),
        ]
    )
    envelope = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert envelope["schema_version"] == "localai.debug/v1"
    assert envelope["run_id"] == "run-replay"
    assert envelope["data"]["verification"]["status"] == "three_star"
    assert envelope["data"]["callback_summary"]["task_completed"]
    assert envelope["data"]["callback_summary"]["verified"] is False


def test_default_adb_discovery_has_no_undefined_candidate(monkeypatch):
    monkeypatch.delenv("MAA_LOCALAI_ADB", raising=False)

    result = _default_adb()

    assert result is None or result.is_file()


def test_preflight_accepts_only_exact_target_stage_detail():
    assert _is_safe_preflight_state("stage_detail_act55side_ex05", "act55side_ex05")
    assert _is_safe_preflight_state("home", "act55side_ex05")
    assert not _is_safe_preflight_state(
        "stage_detail_act55side_ex04",
        "act55side_ex05",
    )
    assert not _is_safe_preflight_state("unknown", "act55side_ex05")


def test_snapshot_image_reads_nested_preflight_metadata():
    assert _snapshot_image(
        {"screenshot": {"path": "preflight.png", "sequence": 1}}
    )["path"] == "preflight.png"


def test_snapshot_image_rejects_wrong_payload_shape():
    try:
        _snapshot_image({"path": "preflight.png"})
    except EvidenceError as exception:
        assert "no captured screenshot" in str(exception)
    else:
        raise AssertionError("invalid snapshot payload was accepted")


def test_result_capture_polls_unknown_frames_without_touching_game(
    tmp_path: Path,
    monkeypatch,
):
    success, payload = cv2.imencode(".png", _three_star_image())
    assert success

    class FakeAdb:
        def __init__(self, **_kwargs):
            self.sequence = 0

        def capture_frame(self, *, force):
            assert force
            self.sequence += 1
            return types.SimpleNamespace(
                payload=payload.tobytes(),
                metadata=lambda: {
                    "sequence": self.sequence,
                    "action_sequence": 0,
                    "captured_at": "now",
                    "cache_hit": False,
                },
            )

    class FakeVerifier:
        def __init__(self):
            self.calls = 0

        def verify(self, _image, **_kwargs):
            self.calls += 1
            status = (
                "unknown"
                if self.calls < 3
                else "non_three_star"
            )
            return ResultVerification(
                status=status,
                stars=None,
                confidence=1.0,
                evidence=(),
                needs_computer_use=status == "unknown",
            )

    instances = []

    def make_adb(**kwargs):
        instance = FakeAdb(**kwargs)
        instances.append(instance)
        return instance

    monkeypatch.setattr(debug_cli, "AdbClient", make_adb)
    sleeps = []
    monkeypatch.setattr(
        debug_cli.time,
        "sleep",
        lambda seconds: sleeps.append(seconds),
    )

    verification, screenshots = _capture_verified_result(
        adb_path=tmp_path / "adb.exe",
        address="emulator-5554",
        destination=tmp_path / "result.png",
        metrics=RoundMetrics(round_name="result", stage="test"),
        verifier=FakeVerifier(),
        maximum_captures=4,
        interval_seconds=2.0,
    )

    assert verification.status == "non_three_star"
    assert len(screenshots) == 3
    assert sleeps == [2.0, 2.0]
    assert (tmp_path / "result-wait-1.png").is_file()
    assert (tmp_path / "result-wait-2.png").is_file()


def test_stop_records_unverified_safe_exit_without_adb(
    tmp_path: Path,
    capsys,
):
    evidence_root = tmp_path / "evidence"
    store = RunStore(
        evidence_root,
        run_id="run-stop-cli",
        attempt_id="attempt-stop-cli",
    )
    store.update_status("RUNNING", stage="stage")

    exit_code = main(
        [
            "stop",
            "--evidence-root",
            str(evidence_root),
            "--run-id",
            "run-stop-cli",
            "--adb",
            str(tmp_path / "missing-adb.exe"),
        ]
    )
    envelope = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert envelope["status"] == "STOPPED_UNVERIFIED"
    assert envelope["data"]["safe_exit_verified"] is False
    assert envelope["data"]["runner_alive"] is False
    assert store.stop_requested()


def test_stop_keeps_live_runner_in_stopping_state(
    tmp_path: Path,
    capsys,
):
    evidence_root = tmp_path / "evidence"
    store = RunStore(
        evidence_root,
        run_id="run-stop-live",
        attempt_id="attempt-stop-live",
    )
    store.update_status(RunPhase.RUNNING, stage="stage")
    store.mark_active()

    exit_code = main(
        [
            "stop",
            "--evidence-root",
            str(evidence_root),
            "--run-id",
            "run-stop-live",
            "--adb",
            str(tmp_path / "missing-adb.exe"),
        ]
    )
    envelope = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert envelope["status"] == "STOPPING_UNVERIFIED"
    assert envelope["data"]["runner_alive"] is True
    status = json.loads(store.status_path.read_text(encoding="utf-8"))
    assert status["status"] == RunPhase.STOPPING.value
    store.clear_active()


def test_run_stage_requires_explicit_formal_confirmation(
    tmp_path: Path,
    capsys,
):
    exit_code = main(
        [
            "run-stage",
            "--stage",
            "stage",
            "--copilot",
            str(tmp_path / "job.json"),
            "--evidence-root",
            str(tmp_path / "evidence"),
        ]
    )
    envelope = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert envelope["status"] == "FAILED"
    assert "confirm-formal-battle" in envelope["errors"][0]


def test_run_stage_persists_preflight_failure_evidence(
    tmp_path: Path,
    capsys,
    monkeypatch,
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "MaaCore.dll").write_bytes(b"not a library")
    copilot = tmp_path / "job.json"
    copilot.write_text(
        json.dumps({"stage_name": "OF-1", "opers": []}),
        encoding="utf-8",
    )
    evidence_root = tmp_path / "evidence"
    monkeypatch.setattr(debug_cli, "_maa_version", lambda _path: "test")
    monkeypatch.setattr(debug_cli, "_maa_processes", lambda: [])
    monkeypatch.setattr(
        debug_cli,
        "AdbClient",
        lambda **_kwargs: type(
            "FailingAdb",
            (),
            {"devices": lambda _self: (_ for _ in ()).throw(AdbError("offline"))},
        )(),
    )

    exit_code = main(
        [
            "run-stage",
            "--stage",
            "OF-1",
            "--copilot",
            str(copilot),
            "--package-dir",
            str(package_dir),
            "--evidence-root",
            str(evidence_root),
            "--adb",
            sys.executable,
            "--confirm-formal-battle",
        ]
    )
    envelope = json.loads(capsys.readouterr().out)
    run_id = envelope["run_id"]

    assert exit_code == 2
    assert envelope["status"] == RunPhase.FAILED.value
    assert envelope["data"]["outcome"] == "FAILED_RUNTIME"
    assert run_id is not None
    assert (evidence_root / run_id / "status.json").is_file()
    assert (evidence_root / run_id / "metrics.json").is_file()
