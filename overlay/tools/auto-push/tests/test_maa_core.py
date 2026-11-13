import re
from pathlib import Path

import pytest

from autopush.maa_core import (
    CoreMessage,
    MaaCoreLibrary,
    MaaEvent,
    MaaCoreSession,
    build_copilot_task,
    extract_oper_box_details,
    summarize_events,
)


MAA_CORE = Path(r"D:\work\CodexOutputs\MAA-LocalAI\MaaCore.dll")
REPOSITORY = Path(__file__).resolve().parents[3]


def test_core_message_ids_match_the_current_cpp_protocol():
    header = (REPOSITORY / "src" / "MaaCore" / "Common" / "AsstMsg.h").read_text(
        encoding="utf-8"
    )
    values = {
        name: int(value.replace("'", ""))
        for name, value in re.findall(
            r"\b(InternalError|InitFailed|ConnectionInfo|AllTasksCompleted"
            r"|AsyncCallInfo|Destroyed|TaskChainError|TaskChainStart"
            r"|TaskChainCompleted|TaskChainExtraInfo|TaskChainStopped"
            r"|SubTaskError|SubTaskStart|SubTaskCompleted|SubTaskExtraInfo"
            r"|SubTaskStopped|ReportRequest)\s*=\s*([0-9']+)",
            header,
        )
    }

    names = {
        "InternalError": "INTERNAL_ERROR",
        "InitFailed": "INIT_FAILED",
        "ConnectionInfo": "CONNECTION_INFO",
        "AllTasksCompleted": "ALL_TASKS_COMPLETED",
        "AsyncCallInfo": "ASYNC_CALL_INFO",
        "Destroyed": "DESTROYED",
        "TaskChainError": "TASK_CHAIN_ERROR",
        "TaskChainStart": "TASK_CHAIN_START",
        "TaskChainCompleted": "TASK_CHAIN_COMPLETED",
        "TaskChainExtraInfo": "TASK_CHAIN_EXTRA_INFO",
        "TaskChainStopped": "TASK_CHAIN_STOPPED",
        "SubTaskError": "SUB_TASK_ERROR",
        "SubTaskStart": "SUB_TASK_START",
        "SubTaskCompleted": "SUB_TASK_COMPLETED",
        "SubTaskExtraInfo": "SUB_TASK_EXTRA_INFO",
        "SubTaskStopped": "SUB_TASK_STOPPED",
        "ReportRequest": "REPORT_REQUEST",
    }
    assert values
    for name, value in values.items():
        assert int(CoreMessage[names[name]]) == value


@pytest.mark.skipif(not MAA_CORE.is_file(), reason="local MAA Core package not found")
def test_maa_core_loads_and_reports_version():
    library = MaaCoreLibrary(MAA_CORE)
    assert library.version()


def test_task_completion_still_requires_result_verification():
    summary = summarize_events(
        [
            MaaEvent(
                CoreMessage.TASK_CHAIN_START,
                {"taskchain": "Copilot", "taskid": 41},
            ),
            MaaEvent(
                CoreMessage.TASK_CHAIN_EXTRA_INFO,
                {
                    "taskchain": "Copilot",
                    "taskid": 41,
                    "what": "CopilotAction",
                    "details": {"name": "Deploy"},
                },
            ),
            MaaEvent(
                CoreMessage.TASK_CHAIN_COMPLETED,
                {"taskchain": "Copilot", "taskid": 41},
            ),
        ]
    )
    assert summary["task_id"] == 41
    assert summary["task_chain"] == "Copilot"
    assert summary["terminal_state"] == "COMPLETED"
    assert summary["phase"] == "VERIFYING"
    assert summary["task_completed"]
    assert summary["copilot_action_count"] == 1
    assert summary["verification_required"]
    assert not summary["verified"]


def test_copilot_task_can_enable_auto_formation_without_sanity_potion(
    tmp_path: Path,
):
    copilot = tmp_path / "job.json"
    operbox = tmp_path / "operbox.json"

    task = build_copilot_task(
        copilot_file=copilot,
        formation=True,
        formation_index=2,
        ignore_requirements=True,
        operbox_data_path=operbox,
    )

    assert task["filename"] == str(copilot.resolve())
    assert task["formation"] is True
    assert task["formation_index"] == 2
    assert task["ignore_requirements"] is True
    assert task["operbox_data_path"] == str(operbox.resolve())
    assert task["use_sanity_potion"] is False
    assert task["exercise"] is False


def test_copilot_task_can_wait_for_settlement_through_single_multi_entry(
    tmp_path: Path,
):
    copilot = tmp_path / "job.json"

    task = build_copilot_task(
        copilot_file=copilot,
        wait_until_end=True,
    )

    assert "filename" not in task
    assert task["copilot_list"] == [
        {"id": 0, "filename": str(copilot.resolve())}
    ]


def test_task_stopped_is_terminal_and_never_success():
    summary = summarize_events(
        [
            MaaEvent(
                CoreMessage.TASK_CHAIN_START,
                {"taskchain": "Copilot", "taskid": 42},
            ),
            MaaEvent(
                CoreMessage.TASK_CHAIN_STOPPED,
                {"taskchain": "Copilot", "taskid": 42},
            ),
        ]
    )

    assert summary["terminal_state"] == "STOPPED"
    assert summary["phase"] == "STOPPED"
    assert not summary["task_completed"]


def test_disconnect_is_terminal_and_never_success():
    summary = summarize_events(
        [
            MaaEvent(
                CoreMessage.TASK_CHAIN_START,
                {"taskchain": "Copilot", "taskid": 43},
            ),
            MaaEvent(
                CoreMessage.CONNECTION_INFO,
                {"what": "Disconnect", "details": {"times": 3}},
            ),
        ]
    )

    assert summary["terminal_state"] == "DISCONNECTED"
    assert summary["phase"] == "FAILED"
    assert summary["connection_errors"]
    assert not summary["task_completed"]


def test_maa_stage_drops_three_star_is_recorded_as_core_evidence():
    summary = summarize_events(
        [
            MaaEvent(
                CoreMessage.TASK_CHAIN_START,
                {"taskchain": "Copilot", "taskid": 44},
            ),
            MaaEvent(
                CoreMessage.SUB_TASK_START,
                {
                    "taskchain": "Copilot",
                    "taskid": 44,
                    "details": {
                        "task": "StageDrops-Stars-3",
                        "result": {
                            "score": 0.9985,
                            "rect": [71, 292, 205, 58],
                            "template": "StageDrops-Stars-3.png",
                        },
                    },
                },
            ),
            MaaEvent(
                CoreMessage.TASK_CHAIN_COMPLETED,
                {"taskchain": "Copilot", "taskid": 44},
            ),
        ]
    )

    assert summary["maa_three_star"] is True
    assert summary["maa_three_star_evidence"][0]["score"] == 0.9985


def test_summary_can_select_one_task_id():
    summary = summarize_events(
        [
            MaaEvent(
                CoreMessage.TASK_CHAIN_START,
                {"taskchain": "StartUp", "taskid": 10},
            ),
            MaaEvent(
                CoreMessage.TASK_CHAIN_COMPLETED,
                {"taskchain": "StartUp", "taskid": 10},
            ),
            MaaEvent(
                CoreMessage.TASK_CHAIN_START,
                {"taskchain": "Copilot", "taskid": 11},
            ),
            MaaEvent(
                CoreMessage.TASK_CHAIN_STOPPED,
                {"taskchain": "Copilot", "taskid": 11},
            ),
        ],
        expected_task_id=11,
    )

    assert summary["task_id"] == 11
    assert summary["task_chain"] == "Copilot"
    assert summary["terminal_state"] == "STOPPED"


def test_extract_oper_box_details_uses_last_complete_payload():
    events = [
        MaaEvent(
            3,
            {
                "what": "OperBoxInfo",
                "details": {"done": False, "own_opers": []},
            },
        ),
        MaaEvent(
            3,
            {
                "what": "OperBoxInfo",
                "details": {
                    "done": True,
                    "own_opers": [{"name": "圣聆初雪", "elite": 2}],
                },
            },
        ),
    ]

    details = extract_oper_box_details(events)

    assert details is not None
    assert details["own_opers"][0]["name"] == "圣聆初雪"


class _FakeCoreApi:
    def __init__(self, running: bool):
        self.running = running
        self.stop_calls = 0

    def AsstRunning(self, _handle):
        return self.running

    def AsstStop(self, _handle):
        self.stop_calls += 1
        self.running = False
        return True


class _FakeCoreLibrary:
    def __init__(self, api: _FakeCoreApi):
        self.library = api


def test_session_stop_requests_core_stop_without_killing_the_game():
    api = _FakeCoreApi(running=True)
    session = object.__new__(MaaCoreSession)
    session.handle = 1
    session.metrics = None
    session._library = _FakeCoreLibrary(api)

    session.stop()

    assert api.stop_calls == 1


def test_session_stop_is_a_no_op_after_task_completion():
    api = _FakeCoreApi(running=False)
    session = object.__new__(MaaCoreSession)
    session.handle = 1
    session.metrics = None
    session._library = _FakeCoreLibrary(api)

    session.stop()

    assert api.stop_calls == 0
