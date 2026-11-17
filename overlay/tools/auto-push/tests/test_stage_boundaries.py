import json
import sys
import types
from pathlib import Path

import cv2
import numpy as np
import pytest

import autopush.debug_cli as debug_cli
from autopush.debug_cli import main
from autopush.maa_core import CoreMessage, MaaEvent
from autopush.states import RunPhase


@pytest.mark.parametrize(
    ("terminal_message", "post_stop_state", "expected_status", "expected_safe"),
    [
        (
            CoreMessage.TASK_CHAIN_STOPPED,
            "home",
            RunPhase.STOPPED.value,
            True,
        ),
        (
            CoreMessage.CONNECTION_INFO,
            None,
            RunPhase.FAILED.value,
            False,
        ),
    ],
)
def test_stage_distinguishes_cooperative_stop_from_connection_loss(
    tmp_path: Path,
    capsys,
    monkeypatch,
    terminal_message,
    post_stop_state,
    expected_status,
    expected_safe,
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "MaaCore.dll").write_bytes(b"not a library")
    copilot = tmp_path / "job.json"
    copilot.write_text(
        json.dumps({"stage_name": "OF-1", "opers": []}),
        encoding="utf-8",
    )

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
        def __init__(self):
            self.calls = 0

        def recognize(self, _image, **_kwargs):
            self.calls += 1
            name = (
                "stage_detail_OF-1"
                if self.calls == 1
                else post_stop_state or "unknown"
            )
            return types.SimpleNamespace(
                name=name,
                known=name != "unknown",
                distance=0.0 if name != "unknown" else 64.0,
                confidence=1.0 if name != "unknown" else 0.0,
                reference_path=None,
            )

    snapshot_count = 0

    def fake_snapshot(store, **kwargs):
        nonlocal snapshot_count
        snapshot_count += 1
        path = (
            store.run_dir
            / "snapshots"
            / f"{snapshot_count}-{kwargs['label']}.png"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        assert cv2.imwrite(
            str(path),
            np.zeros((720, 1280, 3), dtype=np.uint8),
        )
        screenshot = {"path": str(path), "sequence": snapshot_count}
        return {"screenshot": screenshot}, [str(path)]

    event_details = (
        {
            "taskchain": "Copilot",
            "taskid": 44,
            "what": "Disconnect",
        }
        if terminal_message == CoreMessage.CONNECTION_INFO
        else {"taskchain": "Copilot", "taskid": 44}
    )
    events = [
        MaaEvent(
            CoreMessage.TASK_CHAIN_START,
            {"taskchain": "Copilot", "taskid": 44},
        ),
        MaaEvent(
            CoreMessage.SUB_TASK_COMPLETED,
            {
                "taskchain": "Copilot",
                "taskid": 44,
                "details": {"task": "Copilot@BattleStartNormal"},
            },
        ),
        MaaEvent(terminal_message, event_details),
    ]

    def fake_run_copilot_file(**kwargs):
        for event in events:
            kwargs["event_sink"](event)
        return events

    monkeypatch.setattr(debug_cli, "_maa_version", lambda _path: "test")
    monkeypatch.setattr(debug_cli, "_maa_processes", lambda: [])
    monkeypatch.setattr(debug_cli, "DeviceControlLease", FakeLease)
    monkeypatch.setattr(debug_cli, "AdbClient", FakeAdb)
    monkeypatch.setattr(debug_cli, "_snapshot_payload", fake_snapshot)
    monkeypatch.setattr(debug_cli, "run_copilot_file", fake_run_copilot_file)
    monkeypatch.setattr(
        debug_cli,
        "VisualStateMemory",
        types.SimpleNamespace(load=lambda *_args, **_kwargs: FakeMemory()),
    )
    monkeypatch.setattr(
        debug_cli,
        "BattleResultVerifier",
        lambda *_args, **_kwargs: types.SimpleNamespace(),
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
            str(tmp_path / "evidence"),
            "--adb",
            sys.executable,
            "--confirm-formal-battle",
        ]
    )
    envelope = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert envelope["status"] == expected_status
    assert envelope["data"]["formal_battle_started"] is True
    assert envelope["data"]["safe_exit_verified"] is expected_safe
    if expected_status == RunPhase.STOPPED.value:
        assert envelope["data"]["post_stop_state"]["state"]["name"] == "home"
    else:
        assert envelope["data"]["post_stop_state"] is None
