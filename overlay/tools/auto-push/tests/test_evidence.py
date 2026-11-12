from pathlib import Path

import pytest

from autopush.evidence import (
    EvidenceError,
    RunStore,
    classify_outcome,
    ensure_no_active_run,
    find_run_store,
)
from autopush.states import RunPhase


def test_status_and_events_are_persisted_with_stable_ids(tmp_path: Path):
    store = RunStore(tmp_path, run_id="run-test", attempt_id="attempt-test")
    store.create_manifest({"command": "test"})
    store.update_status("RUNNING", stage="stage")
    first = store.append_event("maa_callback", {"what": "TaskChainStart"})
    second = store.append_event("maa_callback", {"what": "TaskChainCompleted"})

    reopened = find_run_store(tmp_path, run_id="run-test")
    status = reopened.status_path.read_text(encoding="utf-8")
    events, next_after = reopened.read_events(after=1, limit=10)

    assert '"status": "RUNNING"' in status
    assert first["run_id"] == second["run_id"] == "run-test"
    assert first["attempt_id"] == second["attempt_id"] == "attempt-test"
    assert [event["data"]["what"] for event in events] == ["TaskChainCompleted"]
    assert next_after == 2


def test_stop_request_is_attached_to_the_run(tmp_path: Path):
    store = RunStore(tmp_path, run_id="run-stop", attempt_id="attempt-stop")
    store.update_status("RUNNING")

    request = store.request_stop(reason="test", requested_by="pytest")

    assert store.stop_requested()
    assert store.read_stop_request() == request
    events, _ = store.read_events()
    assert events[-1]["type"] == "stop_requested"


def test_active_run_guard_is_released(tmp_path: Path):
    store = RunStore(tmp_path, run_id="run-active", attempt_id="attempt-active")
    store.update_status("RUNNING")
    store.mark_active()

    with pytest.raises(EvidenceError, match="active run"):
        ensure_no_active_run(tmp_path)

    store.update_status(RunPhase.SUCCEEDED)
    store.clear_active()
    ensure_no_active_run(tmp_path)


def test_stale_active_lock_is_recovered(tmp_path: Path):
    lock_path = tmp_path / "active-run.lock.json"
    lock_path.write_text(
        '{"run_id":"dead","attempt_id":"dead","pid":2147483647}',
        encoding="utf-8",
    )
    store = RunStore(
        tmp_path,
        run_id="run-recovered",
        attempt_id="attempt-recovered",
    )

    store.mark_active()

    assert '"run_id": "run-recovered"' in lock_path.read_text(encoding="utf-8")
    store.clear_active()
    assert not lock_path.exists()


@pytest.mark.parametrize(
    (
        "stop_requested",
        "task_completed",
        "task_errors",
        "connection_errors",
        "verification",
        "expected",
    ),
    [
        (True, True, [], [], "three_star", RunPhase.STOPPED),
        (False, False, [], [], "three_star", RunPhase.UNKNOWN),
        (
            False,
            True,
            [{"what": "TaskChainError"}],
            [],
            "three_star",
            RunPhase.FAILED,
        ),
        (False, True, [], [], "three_star", RunPhase.SUCCEEDED),
        (False, True, [], [], "two_star", RunPhase.FAILED),
        (False, True, [], [], "unknown", RunPhase.UNKNOWN),
        (False, True, [], [], None, RunPhase.UNKNOWN),
        (
            False,
            True,
            [],
            [{"what": "Disconnect"}],
            "three_star",
            RunPhase.FAILED,
        ),
    ],
)
def test_outcome_never_promotes_task_completion_to_three_stars(
    stop_requested: bool,
    task_completed: bool,
    task_errors: list[dict],
    connection_errors: list[dict],
    verification: str | None,
    expected: RunPhase,
):
    assert (
        classify_outcome(
            stop_requested=stop_requested,
            task_completed=task_completed,
            task_errors=task_errors,
            connection_errors=connection_errors,
            verification_status=verification,
        )
        == expected
    )


def test_invalid_run_id_is_rejected(tmp_path: Path):
    with pytest.raises(EvidenceError, match="invalid run_id"):
        RunStore(tmp_path, run_id="../escape")
