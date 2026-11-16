import json
import sys
import types
from pathlib import Path

import cv2
import numpy as np

import autopush.debug_cli as debug_cli
from autopush.adb import AdbError
from autopush.campaign import CampaignPolicy, CampaignStore
from autopush.debug_cli import (
    _default_adb,
    _capture_verified_result,
    _is_safe_preflight_state,
    _is_safe_post_stop_state,
    _snapshot_image,
    main,
)
from autopush.evidence import EvidenceError
from autopush.maa_core import CoreMessage, MaaEvent
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
    assert envelope["data"]["transition_trace"][0]["phase"] == "RUNNING"
    assert envelope["data"]["transition_trace"][-1]["phase"] == "VERIFYING"
    assert envelope["data"]["evidence_resolution"]["status"] == "three_star"


def test_default_adb_discovery_has_no_undefined_candidate(monkeypatch):
    monkeypatch.delenv("MAA_LOCALAI_ADB", raising=False)

    result = _default_adb()

    assert result is None or result.is_file()


def test_preflight_accepts_only_exact_target_stage_detail():
    assert _is_safe_preflight_state("stage_detail_act55side_ex05", "act55side_ex05")
    assert _is_safe_preflight_state("home", "act55side_ex05")
    assert _is_safe_preflight_state("event_map", "act55side_ex06")
    assert not _is_safe_preflight_state(
        "stage_detail_act55side_ex04",
        "act55side_ex05",
    )
    assert not _is_safe_preflight_state("unknown", "act55side_ex05")


def test_post_stop_accepts_only_known_safe_pages():
    assert _is_safe_post_stop_state("home", "OF-1")
    assert _is_safe_post_stop_state("stage_detail_OF-1", "OF-1")
    assert not _is_safe_post_stop_state("stage_detail_OF-2", "OF-1")
    assert not _is_safe_post_stop_state("unknown", "OF-1")


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


def test_run_campaign_uses_deterministic_ranking_and_stops_on_success(
    tmp_path: Path,
    monkeypatch,
):
    (tmp_path / "adb.exe").write_bytes(b"adb")
    jobs_root = tmp_path / "jobs"
    (jobs_root / "act55side_ex06").mkdir(parents=True)
    candidate = types.SimpleNamespace(
        strategy=types.SimpleNamespace(
            strategy_id=str(jobs_root / "act55side_ex06" / "job.json"),
            raw={},
        ),
        runnable=True,
        requirements_satisfied=True,
        missing=(),
        diagnostics=(),
        lineup=(),
    )
    monkeypatch.setattr(debug_cli, "load_roster", lambda _path: {})
    monkeypatch.setattr(
        debug_cli,
        "collect_strategy_files",
        lambda _paths: [Path(candidate.strategy.strategy_id)],
    )
    monkeypatch.setattr(
        debug_cli,
        "rank_strategies",
        lambda *_args, **_kwargs: [candidate],
    )
    captured_configs = []

    def run_stage(config):
        captured_configs.append(config)
        return (
            {
                "schema_version": "localai.debug/v1",
                "command": "run-stage",
                "status": "SUCCEEDED",
                "run_id": "run-campaign",
                "attempt_id": "attempt-campaign",
                "data": {"outcome": "SUCCEEDED"},
                "errors": [],
            },
            0,
        )

    monkeypatch.setattr(debug_cli, "_run_stage", run_stage)

    arguments = types.SimpleNamespace(
        command="run-campaign",
        confirm_formal_battle=True,
        package_dir=str(tmp_path / "package"),
        evidence_root=str(tmp_path / "evidence"),
        dll=None,
        resource_dir=None,
        user_dir=None,
        template_root=None,
        state_memory_dir=None,
        adb=str(tmp_path / "adb.exe"),
        address="emulator-5554",
        connect_config="General",
        timeout=600,
        stages=["act55side_ex06"],
        jobs_root=str(jobs_root),
        roster=str(tmp_path / "OperBoxData.json"),
        operbox_data=None,
        round_name="test-campaign",
        verify_threshold=0.91,
        campaign_id="campaign-success",
        resume_campaign=False,
        recovery_evidence=None,
        max_attempts_per_stage=2,
        max_total_attempts=3,
        max_elapsed_seconds=3600,
        max_sanity=42,
        sanity_cost=["act55side_ex06=21"],
    )

    envelope, exit_code = debug_cli._run_campaign(arguments)

    assert exit_code == 0
    assert envelope["status"] == "OK"
    assert envelope["data"]["attempts"][0]["stage"] == "act55side_ex06"
    assert envelope["data"]["attempts"][0]["status"] == "SUCCEEDED"
    assert captured_configs[0].verify_threshold == 0.91
    assert captured_configs[0].ignore_requirements is False


def test_run_campaign_blocks_degraded_requirements_before_formal_battle(
    tmp_path: Path,
    monkeypatch,
):
    (tmp_path / "adb.exe").write_bytes(b"adb")
    jobs_root = tmp_path / "jobs"
    (jobs_root / "act55side_ex06").mkdir(parents=True)
    candidate = types.SimpleNamespace(
        strategy=types.SimpleNamespace(
            strategy_id=str(jobs_root / "act55side_ex06" / "job.json"),
            raw={},
        ),
        status="degraded",
        requirements_satisfied=False,
        missing=(),
        diagnostics=("A: level 1 < 90",),
        lineup=(),
    )
    monkeypatch.setattr(debug_cli, "load_roster", lambda _path: {})
    monkeypatch.setattr(
        debug_cli,
        "rank_strategies",
        lambda *_args, **_kwargs: [candidate],
    )
    run_stage_calls = 0

    def run_stage(_config):
        nonlocal run_stage_calls
        run_stage_calls += 1
        raise AssertionError("formal battle must not start")

    monkeypatch.setattr(debug_cli, "_run_stage", run_stage)
    arguments = types.SimpleNamespace(
        command="run-campaign",
        confirm_formal_battle=True,
        package_dir=str(tmp_path / "package"),
        evidence_root=str(tmp_path / "evidence"),
        dll=None,
        resource_dir=None,
        user_dir=None,
        template_root=None,
        state_memory_dir=None,
        adb=str(tmp_path / "adb.exe"),
        address="emulator-5554",
        connect_config="General",
        timeout=600,
        stages=["act55side_ex06"],
        jobs_root=str(jobs_root),
        roster=str(tmp_path / "OperBoxData.json"),
        operbox_data=None,
        round_name="test-campaign-requirements",
        verify_threshold=0.86,
        campaign_id="campaign-blocked",
        resume_campaign=False,
        recovery_evidence=None,
        max_attempts_per_stage=2,
        max_total_attempts=3,
        max_elapsed_seconds=3600,
        max_sanity=42,
        sanity_cost=["act55side_ex06=21"],
    )

    envelope, exit_code = debug_cli._run_campaign(arguments)

    assert exit_code == 2
    assert envelope["status"] == "BLOCKED"
    assert "no runnable job" in envelope["errors"][0]
    assert run_stage_calls == 0


def test_run_campaign_switches_candidates_until_three_star(
    tmp_path: Path,
    monkeypatch,
):
    (tmp_path / "adb.exe").write_bytes(b"adb")
    jobs_root = tmp_path / "jobs"
    (jobs_root / "OF-1").mkdir(parents=True)
    candidates = [
        types.SimpleNamespace(
            strategy=types.SimpleNamespace(
                strategy_id=str(tmp_path / f"job-{index}.json"),
                raw={},
            ),
            status="ready",
            requirements_satisfied=True,
            missing=(),
            diagnostics=(),
            lineup=(),
        )
        for index in range(2)
    ]
    monkeypatch.setattr(debug_cli, "load_roster", lambda _path: {})
    monkeypatch.setattr(
        debug_cli,
        "rank_strategies",
        lambda *_args, **_kwargs: candidates,
    )
    selected_jobs: list[str] = []

    def run_stage(config):
        selected_jobs.append(config.copilot)
        if len(selected_jobs) == 1:
            return (
                {
                    "status": "FAILED",
                    "run_id": "run-failed",
                    "attempt_id": "attempt-failed",
                    "data": {
                        "outcome": "FAILED",
                        "formal_battle_started": True,
                        "verification_status": "non_three_star",
                    },
                    "errors": [],
                },
                2,
            )
        return (
            {
                "status": "SUCCEEDED",
                "run_id": "run-success",
                "attempt_id": "attempt-success",
                "data": {
                    "outcome": "SUCCEEDED",
                    "formal_battle_started": True,
                },
                "errors": [],
            },
            0,
        )

    monkeypatch.setattr(debug_cli, "_run_stage", run_stage)
    arguments = types.SimpleNamespace(
        command="run-campaign",
        confirm_formal_battle=True,
        package_dir=str(tmp_path / "package"),
        evidence_root=str(tmp_path / "evidence"),
        dll=None,
        resource_dir=None,
        user_dir=None,
        template_root=None,
        state_memory_dir=None,
        adb=str(tmp_path / "adb.exe"),
        address="emulator-5554",
        connect_config="General",
        timeout=600,
        verify_threshold=0.86,
        stages=["OF-1"],
        jobs_root=str(jobs_root),
        roster=str(tmp_path / "OperBoxData.json"),
        operbox_data=None,
        round_name="test-campaign-switch",
        campaign_id="campaign-switch",
        resume_campaign=False,
        recovery_evidence=None,
        max_attempts_per_stage=3,
        max_total_attempts=4,
        max_elapsed_seconds=3600,
        max_sanity=42,
        sanity_cost=["OF-1=21"],
    )

    envelope, exit_code = debug_cli._run_campaign(arguments)

    assert exit_code == 0
    assert selected_jobs == [
        candidates[0].strategy.strategy_id,
        candidates[1].strategy.strategy_id,
    ]
    assert envelope["data"]["sanity_spent"] == 42
    assert [item["status"] for item in envelope["data"]["attempts"]] == [
        "FAILED",
        "SUCCEEDED",
    ]
    state = json.loads(
        (
            tmp_path
            / "evidence"
            / "_campaigns"
            / "campaign-switch"
            / "state.json"
        ).read_text(encoding="utf-8")
    )
    assert state["status"] == "SUCCEEDED"


def test_run_campaign_pauses_unknown_and_resumes_from_checkpoint(
    tmp_path: Path,
    monkeypatch,
):
    (tmp_path / "adb.exe").write_bytes(b"adb")
    jobs_root = tmp_path / "jobs"
    (jobs_root / "OF-1").mkdir(parents=True)
    candidate = types.SimpleNamespace(
        strategy=types.SimpleNamespace(
            strategy_id=str(tmp_path / "job.json"),
            raw={},
        ),
        status="ready",
        requirements_satisfied=True,
        missing=(),
        diagnostics=(),
        lineup=(),
    )
    replacement = types.SimpleNamespace(
        strategy=types.SimpleNamespace(
            strategy_id=str(tmp_path / "replacement.json"),
            raw={},
        ),
        status="ready",
        requirements_satisfied=True,
        missing=(),
        diagnostics=(),
        lineup=(),
    )
    monkeypatch.setattr(debug_cli, "load_roster", lambda _path: {})
    monkeypatch.setattr(
        debug_cli,
        "rank_strategies",
        lambda *_args, **_kwargs: [candidate, replacement],
    )
    run_count = 0

    def run_stage(_config):
        nonlocal run_count
        run_count += 1
        return (
            {
                "status": "UNKNOWN" if run_count == 1 else "SUCCEEDED",
                "run_id": f"run-{run_count}",
                "attempt_id": f"attempt-{run_count}",
                "data": {
                    "outcome": "UNKNOWN" if run_count == 1 else "SUCCEEDED",
                    "formal_battle_started": True,
                },
                "errors": [],
            },
            2 if run_count == 1 else 0,
        )

    monkeypatch.setattr(debug_cli, "_run_stage", run_stage)
    arguments = types.SimpleNamespace(
        command="run-campaign",
        confirm_formal_battle=True,
        package_dir=str(tmp_path / "package"),
        evidence_root=str(tmp_path / "evidence"),
        dll=None,
        resource_dir=None,
        user_dir=None,
        template_root=None,
        state_memory_dir=None,
        adb=str(tmp_path / "adb.exe"),
        address="emulator-5554",
        connect_config="General",
        timeout=600,
        verify_threshold=0.86,
        stages=["OF-1"],
        jobs_root=str(jobs_root),
        roster=str(tmp_path / "OperBoxData.json"),
        operbox_data=None,
        round_name="test-campaign-resume",
        campaign_id="campaign-resume",
        resume_campaign=False,
        recovery_evidence=None,
        max_attempts_per_stage=3,
        max_total_attempts=3,
        max_elapsed_seconds=3600,
        max_sanity=63,
        sanity_cost=["OF-1=21"],
    )

    paused, paused_exit = debug_cli._run_campaign(arguments)
    blocked_resume, blocked_exit = debug_cli._run_campaign(arguments)
    assert paused_exit == 2
    assert paused["status"] == "PAUSED"
    assert blocked_exit == 2
    assert blocked_resume["status"] == "PAUSED"
    assert run_count == 1

    arguments.resume_campaign = True
    arguments.recovery_evidence = "state-memory/home"
    resumed, resumed_exit = debug_cli._run_campaign(arguments)

    assert resumed_exit == 0
    assert resumed["status"] == "OK"
    assert run_count == 2
    assert resumed["data"]["campaign"]["recovery_evidence"] == (
        "state-memory/home"
    )


def test_campaign_status_reads_persisted_queue(
    tmp_path: Path,
    capsys,
):
    evidence_root = tmp_path / "evidence"
    store = CampaignStore(evidence_root, "campaign-status")
    store.create(
        stages=["OF-1"],
        policy=CampaignPolicy(
            sanity_costs={"OF-1": 21},
            max_sanity=42,
        ),
    )

    exit_code = main(
        [
            "campaign-status",
            "--evidence-root",
            str(evidence_root),
            "--campaign-id",
            "campaign-status",
        ]
    )
    envelope = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert envelope["status"] == "ACTIVE"
    assert envelope["data"]["campaign"]["stages"] == ["OF-1"]
    assert Path(envelope["artifacts"][0]).is_file()


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


def test_run_stage_rejects_a_job_for_another_stage_with_evidence(
    tmp_path: Path,
    capsys,
    monkeypatch,
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "MaaCore.dll").write_bytes(b"not a library")
    copilot = tmp_path / "job.json"
    copilot.write_text(
        json.dumps({"stage_name": "OF-2", "opers": []}),
        encoding="utf-8",
    )
    evidence_root = tmp_path / "evidence"
    monkeypatch.setattr(debug_cli, "_maa_version", lambda _path: "test")
    monkeypatch.setattr(debug_cli, "_maa_processes", lambda: [])

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
    assert envelope["status"] == "BLOCKED"
    assert envelope["data"]["outcome"] == "BLOCKED_STAGE_MISMATCH"
    assert "requested='OF-1'" in envelope["errors"][0]
    status = json.loads(
        (evidence_root / run_id / "status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == RunPhase.UNKNOWN.value
    assert status["outcome"] == "BLOCKED_STAGE_MISMATCH"


def test_settlement_is_captured_before_core_returns_and_conflict_is_unknown(
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
    timeline: list[str] = []

    class FakeLease:
        def __init__(self, *_args, **_kwargs):
            pass

        def acquire(self):
            return None

        def release(self):
            return None

    class FakeAdb:
        def __init__(self, **_kwargs):
            pass

        def devices(self):
            return ["emulator-5554"]

    class FakeMemory:
        @staticmethod
        def recognize(_image, **_kwargs):
            return types.SimpleNamespace(
                name="home",
                known=True,
                distance=0.0,
                confidence=1.0,
                reference_path=None,
            )

    class FakeVerifier:
        def __init__(self, _root, *, threshold):
            assert threshold == 0.91

        def verify(self, _image, **_kwargs):
            return ResultVerification(
                status="two_star",
                stars=2,
                confidence=0.97,
                evidence=(),
                needs_computer_use=False,
            )

    def fake_snapshot(store, **_kwargs):
        path = store.run_dir / "snapshots" / "preflight.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        assert cv2.imwrite(
            str(path),
            np.zeros((720, 1280, 3), dtype=np.uint8),
        )
        screenshot = {"path": str(path), "sequence": 1}
        return {"screenshot": screenshot}, [str(path)]

    def fake_capture(**kwargs):
        timeline.append("settlement-capture")
        destination = kwargs["destination"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        assert cv2.imwrite(
            str(destination),
            np.zeros((720, 1280, 3), dtype=np.uint8),
        )
        return {
            "path": str(destination),
            "sequence": 2,
            "captured_at": "now",
        }

    events = [
        MaaEvent(
            CoreMessage.TASK_CHAIN_START,
            {"taskchain": "Copilot", "taskid": 7},
        ),
        MaaEvent(
            CoreMessage.SUB_TASK_START,
            {
                "taskchain": "Copilot",
                "taskid": 7,
                "details": {
                    "task": "Copilot@StageDrops-Stars-3",
                    "result": {"score": 0.99},
                },
            },
        ),
        MaaEvent(
            CoreMessage.TASK_CHAIN_COMPLETED,
            {"taskchain": "Copilot", "taskid": 7},
        ),
    ]

    def fake_run_copilot_file(**kwargs):
        event_sink = kwargs["event_sink"]
        for event in events:
            event_sink(event)
        timeline.append("core-run-returned")
        return events

    monkeypatch.setattr(debug_cli, "_maa_version", lambda _path: "test")
    monkeypatch.setattr(debug_cli, "_maa_processes", lambda: [])
    monkeypatch.setattr(debug_cli, "DeviceControlLease", FakeLease)
    monkeypatch.setattr(debug_cli, "AdbClient", FakeAdb)
    monkeypatch.setattr(debug_cli, "_snapshot_payload", fake_snapshot)
    monkeypatch.setattr(debug_cli, "_capture_screenshot", fake_capture)
    monkeypatch.setattr(
        debug_cli,
        "VisualStateMemory",
        types.SimpleNamespace(
            load=lambda *_args, **_kwargs: FakeMemory()
        ),
    )
    monkeypatch.setattr(debug_cli, "BattleResultVerifier", FakeVerifier)
    monkeypatch.setattr(debug_cli, "run_copilot_file", fake_run_copilot_file)

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
            str(tmp_path / "evidence"),
            "--adb",
            sys.executable,
            "--verify-threshold",
            "0.91",
            "--confirm-formal-battle",
        ]
    )
    envelope = json.loads(capsys.readouterr().out)

    assert timeline == ["settlement-capture", "core-run-returned"]
    assert exit_code == 2
    assert envelope["status"] == RunPhase.UNKNOWN.value
    assert envelope["data"]["verification_status"] == "conflict"
    assert envelope["data"]["verification_conflict"] is True
    status = json.loads(
        (
            tmp_path
            / "evidence"
            / envelope["run_id"]
            / "status.json"
        ).read_text(encoding="utf-8")
    )
    assert status["status"] == RunPhase.UNKNOWN.value
    assert status["verification"]["visual_evidence"]["status"] == "two_star"
    assert status["verification"]["maa_core_evidence"][0]["score"] == 0.99
