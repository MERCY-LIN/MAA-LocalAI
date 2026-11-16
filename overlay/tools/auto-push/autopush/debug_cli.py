"""JSON-first debugging CLI for LocalAI runs and saved evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

import cv2

from .adb import AdbClient, AdbError
from .campaign import (
    CampaignAttempt,
    CampaignError,
    CampaignPolicy,
    CampaignState,
    CampaignStore,
    parse_sanity_costs,
)
from .device_control import DeviceBusyError, DeviceControlLease
from .execution import DEFAULT_VERIFY_THRESHOLD, StageExecutionConfig
from .evidence import (
    ACTIVE_RUN_FILE,
    EvidenceError,
    RunStore,
    classify_outcome,
    ensure_no_active_run,
    find_run_store,
    new_run_id,
    process_is_running,
    read_json,
    result_envelope,
    utc_now,
    write_json_atomic,
)
from .maa_core import (
    MaaCoreError,
    MaaEvent,
    MaaCoreLibrary,
    event_has_three_star_result,
    run_copilot_file,
    summarize_events,
)
from .metrics import BudgetExceeded, BudgetLimits, RoundMetrics, TestResult
from .planner import load_roster
from .recognizer import decode_png
from .recognition_acceptance import (
    report_payload,
    run_recognition_corpus,
)
from .state_memory import VisualStateMemory
from .states import RunPhase
from .strategy import (
    CopilotStrategy,
    candidate_has_hard_requirement_gap,
    collect_strategy_files,
    job_is_raid_only,
    rank_strategies,
)
from .verifier import (
    BattleResultVerifier,
    ResultVerification,
    resolve_battle_evidence,
)


DEFAULT_PACKAGE_DIR = Path(r"D:\work\CodexOutputs\MAA-LocalAI")
DEFAULT_ADB_CANDIDATES = (
    Path(r"D:\Program Files\Netease\MuMu\nx_main\adb.exe"),
)
PROCESS_NAMES = {
    "maa.exe",
    "mumuVMMHeadless.exe".casefold(),
    "mumuNxDevice.exe".casefold(),
    "mumuNxMain.exe".casefold(),
    "ollama.exe",
}
ACTIVE_STATUSES = {
    RunPhase.PREPARING.value,
    RunPhase.RUNNING.value,
    RunPhase.VERIFYING.value,
    RunPhase.STOPPING.value,
}
SAFE_PREFLIGHT_STATES = {
    "agreement",
    "credit_shop",
    "event_map",
    "home",
    "settings",
    "terminal_protocol",
}
SAFE_POST_STOP_STATES = {
    "agreement",
    "credit_shop",
    "event_map",
    "home",
    "settings",
    "terminal_protocol",
}
TARGET_STAGE_DETAIL_PREFIX = "stage_detail_"


def _is_safe_preflight_state(state_name: str, stage: str) -> bool:
    """Accept generic safe pages or the exact target's stage-detail page."""

    return (
        state_name in SAFE_PREFLIGHT_STATES
        or state_name == f"{TARGET_STAGE_DETAIL_PREFIX}{stage}"
    )


def _is_safe_post_stop_state(state_name: str, stage: str) -> bool:
    return (
        state_name in SAFE_POST_STOP_STATES
        or state_name == f"{TARGET_STAGE_DETAIL_PREFIX}{stage}"
    )


def _resolve(value: str | None, default: Path) -> Path:
    return Path(value).expanduser().resolve() if value else default.resolve()


def _default_package_dir() -> Path:
    value = os.environ.get("MAA_LOCALAI_PACKAGE_DIR")
    return Path(value) if value else DEFAULT_PACKAGE_DIR


def _default_evidence_root() -> Path:
    return _default_package_dir() / "data" / "autopush" / "evidence"


def _default_adb() -> Path | None:
    value = os.environ.get("MAA_LOCALAI_ADB")
    candidates = (Path(value), *DEFAULT_ADB_CANDIDATES) if value else (
        _default_package_dir() / "adb.exe",
        *DEFAULT_ADB_CANDIDATES,
    )
    return next((path for path in candidates if path.is_file()), None)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_version(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "last_write_time": datetime.fromtimestamp(
            stat.st_mtime,
            tz=UTC,
        ).isoformat(),
        "sha256": _hash_file(path),
    }


def _resource_version(package_dir: Path) -> dict[str, Any] | None:
    path = package_dir / "resource" / "version.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"path": str(path.resolve()), "parse_error": True}
    return {
        "path": str(path.resolve()),
        **value,
    }


def _maa_version(dll_path: Path) -> str | None:
    if not dll_path.is_file():
        return None
    try:
        return MaaCoreLibrary(dll_path).version()
    except (OSError, MaaCoreError):
        return None


def _processes() -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    result: list[dict[str, Any]] = []
    for row in csv.reader(completed.stdout.splitlines()):
        if len(row) < 2:
            continue
        name = row[0]
        if name.casefold() not in PROCESS_NAMES:
            continue
        try:
            pid = int(row[1])
        except ValueError:
            continue
        result.append({"name": name, "pid": pid})
    return result


def _maa_processes() -> list[dict[str, Any]]:
    return [
        process
        for process in _processes()
        if process["name"].casefold() == "maa.exe"
    ]


def _local_ai_settings(package_dir: Path) -> dict[str, Any] | None:
    config_path = package_dir / "config" / "gui.new.json"
    value = read_json(config_path)
    if value is None:
        return None
    settings = value.get("LocalAi")
    return settings if isinstance(settings, dict) else None


def _endpoint_check(settings: dict[str, Any] | None) -> dict[str, Any]:
    if not settings:
        return {
            "level": "WARN",
            "detail": "LocalAi settings were not found in gui.new.json",
        }
    endpoint = str(settings.get("Endpoint") or "")
    model = str(settings.get("Model") or "")
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return {
            "level": "WARN",
            "detail": f"invalid endpoint: {endpoint}",
            "model": model,
        }
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return {
            "level": "WARN",
            "detail": f"non-loopback endpoint is configured: {endpoint}",
            "model": model,
        }
    tags_url = urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, "/api/tags", "", "", "")
    )
    try:
        with urllib.request.urlopen(tags_url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exception:
        return {
            "level": "WARN",
            "detail": f"Ollama API unavailable: {exception}",
            "endpoint": endpoint,
            "model": model,
        }
    installed = {
        str(item.get("name"))
        for item in payload.get("models", [])
        if isinstance(item, dict)
    }
    if model and model not in installed:
        return {
            "level": "WARN",
            "detail": f"configured model is not installed: {model}",
            "endpoint": endpoint,
            "model": model,
        }
    return {
        "level": "OK",
        "detail": f"loopback model endpoint reachable; model={model}",
        "endpoint": endpoint,
        "model": model,
    }


def _doctor(arguments: argparse.Namespace) -> tuple[dict[str, Any], int]:
    package_dir = _resolve(arguments.package_dir, _default_package_dir())
    evidence_root = _resolve(arguments.evidence_root, _default_evidence_root())
    store = RunStore(evidence_root)
    dll_path = package_dir / "MaaCore.dll"
    adb_path = _resolve(arguments.adb, Path(arguments.adb)) if arguments.adb else (
        _default_adb()
    )
    checks: list[dict[str, Any]] = []
    required = {
        "maa_exe": package_dir / "MAA.exe",
        "maa_core": dll_path,
        "resource_config": package_dir / "resource" / "config.json",
        "package_config": package_dir / "config" / "gui.new.json",
    }
    for name, path in required.items():
        checks.append(
            {
                "name": name,
                "level": "OK" if path.is_file() else "FAIL",
                "detail": str(path),
            }
        )

    core_version = _maa_version(dll_path)
    checks.append(
        {
            "name": "maa_core_version",
            "level": "OK" if core_version else "FAIL",
            "detail": core_version or "unable to load MaaCore.dll",
        }
    )

    if adb_path and adb_path.is_file():
        metrics = RoundMetrics(
            round_name="localai-debug-doctor",
            stage="doctor",
            limits=BudgetLimits(tool_calls=20, screenshots=0),
        )
        try:
            devices = AdbClient(
                adb_path=adb_path,
                serial=arguments.address,
                metrics=metrics,
            ).devices()
            ready = arguments.address in devices
            checks.append(
                {
                    "name": "adb_device",
                    "level": "OK" if ready else "WARN",
                    "detail": (
                        f"{arguments.address} is connected"
                        if ready
                        else f"ready devices: {', '.join(devices) or 'none'}"
                    ),
                    "adb": str(adb_path.resolve()),
                }
            )
        except (AdbError, OSError, subprocess.TimeoutExpired) as exception:
            checks.append(
                {
                    "name": "adb_device",
                    "level": "WARN",
                    "detail": str(exception),
                    "adb": str(adb_path.resolve()),
                }
            )
    else:
        checks.append(
            {
                "name": "adb_device",
                "level": "WARN",
                "detail": "ADB executable was not found",
            }
        )

    process_list = _processes()
    if _maa_processes():
        checks.append(
            {
                "name": "process_conflict",
                "level": "WARN",
                "detail": "MAA.exe is running; formal run-stage is blocked",
            }
        )
    else:
        checks.append(
            {
                "name": "process_conflict",
                "level": "OK",
                "detail": "MAA.exe is not running",
            }
        )
    lease = DeviceControlLease(arguments.address, timeout_seconds=0)
    try:
        lease.acquire()
    except DeviceBusyError as exception:
        checks.append(
            {
                "name": "device_control",
                "level": "WARN",
                "detail": str(exception),
            }
        )
    else:
        lease.release()
        checks.append(
            {
                "name": "device_control",
                "level": "OK",
                "detail": "device control mutex is available",
            }
        )
    checks.append(_endpoint_check(_local_ai_settings(package_dir)))

    status = (
        "FAILED"
        if any(item["level"] == "FAIL" for item in checks)
        else "UNVERIFIED"
        if any(item["level"] == "WARN" for item in checks)
        else "OK"
    )
    data = {
        "package_dir": str(package_dir),
        "evidence_root": str(evidence_root),
        "resource_version": _resource_version(package_dir),
        "binaries": {
            "maa_exe": _file_version(package_dir / "MAA.exe"),
            "maa_core": _file_version(dll_path),
        },
        "processes": process_list,
        "checks": checks,
    }
    store.create_manifest(
        {
            "command": "doctor",
            "package_dir": str(package_dir),
            "checks": checks,
        }
    )
    store.update_status(status, checks=checks)
    artifacts = [str(store.manifest_path), str(store.status_path)]
    return (
        result_envelope(
            "doctor",
            status,
            run_id=store.run_id,
            attempt_id=store.attempt_id,
            data=data,
            artifacts=artifacts,
        ),
        0 if status != "FAILED" else 2,
    )


def _capture_screenshot(
    *,
    adb_path: Path,
    address: str,
    destination: Path,
    metrics: RoundMetrics,
    client: AdbClient | None = None,
) -> dict[str, Any]:
    capture_client = client or AdbClient(
        adb_path=adb_path,
        serial=address,
        metrics=metrics,
        minimum_capture_interval_seconds=0,
    )
    frame = capture_client.capture_frame(force=True)
    image = decode_png(frame.payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(frame.payload)
    return {
        "path": str(destination.resolve()),
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
        "sha256": hashlib.sha256(frame.payload).hexdigest(),
        **frame.metadata(),
    }


def _capture_verified_result(
    *,
    adb_path: Path,
    address: str,
    destination: Path,
    metrics: RoundMetrics,
    verifier: BattleResultVerifier,
    maximum_captures: int = 10,
    interval_seconds: float = 6.0,
) -> tuple[Any, list[dict[str, Any]]]:
    """Poll for a real settlement screen without touching the battle UI."""

    client = AdbClient(
        adb_path=adb_path,
        serial=address,
        metrics=metrics,
        minimum_capture_interval_seconds=0,
    )
    screenshots: list[dict[str, Any]] = []
    verification = None
    for attempt in range(maximum_captures):
        if attempt:
            time.sleep(interval_seconds)
        capture_path = (
            destination
            if attempt == 0
            else destination.with_name(
                f"{destination.stem}-wait-{attempt}{destination.suffix}"
            )
        )
        screenshots.append(
            _capture_screenshot(
                adb_path=adb_path,
                address=address,
                destination=capture_path,
                metrics=metrics,
                client=client,
            )
        )
        decoded = cv2.imread(str(capture_path), cv2.IMREAD_COLOR)
        if decoded is None:
            raise ValueError("unable to decode result screenshot")
        verification = verifier.verify(decoded, metrics=metrics)
        if verification.status != "unknown":
            break
    return verification, screenshots


def _capture_single_result(
    *,
    adb_path: Path,
    address: str,
    destination: Path,
    metrics: RoundMetrics,
    verifier: BattleResultVerifier,
) -> tuple[dict[str, Any], ResultVerification]:
    screenshot = _capture_screenshot(
        adb_path=adb_path,
        address=address,
        destination=destination,
        metrics=metrics,
    )
    image = cv2.imread(str(destination), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("unable to decode settlement screenshot")
    return screenshot, verifier.verify(image, metrics=metrics)


def _formation_from_copilot(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {
            "status": "UNKNOWN",
            "reason": "copilot file was not supplied",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exception:
        return {"status": "UNKNOWN", "reason": str(exception)}
    if isinstance(payload, dict) and isinstance(payload.get("content"), str):
        try:
            payload = json.loads(payload["content"])
        except json.JSONDecodeError as exception:
            return {"status": "UNKNOWN", "reason": str(exception)}
    operators = payload.get("opers")
    if not isinstance(operators, list):
        operators = []
    formation = []
    for operator in operators:
        if not isinstance(operator, dict):
            continue
        formation.append(
            {
                "name": operator.get("name"),
                "operator": operator.get("operator"),
                "skill": operator.get("skill"),
                "skill_usage": operator.get("skill_usage"),
                "direction": operator.get("direction"),
                "location": operator.get("location"),
            }
        )
    return {
        "status": "KNOWN" if formation else "UNKNOWN",
        "stage_name": payload.get("stage_name"),
        "doc": payload.get("doc"),
        "operators": formation,
        "copilot_sha256": _hash_file(path),
    }


def _validate_stage_job(
    copilot_path: Path,
    stage: str,
) -> CopilotStrategy:
    """Load one Copilot plan and require exact stage identity."""

    strategy = CopilotStrategy.load(copilot_path)
    if not strategy.stage_id:
        raise EvidenceError(
            f"Copilot job has no stage_name: {copilot_path}"
        )
    if strategy.stage_id != stage:
        raise EvidenceError(
            "Copilot stage mismatch: "
            f"requested={stage!r}, job={strategy.stage_id!r}"
        )
    return strategy


def _snapshot_payload(
    store: RunStore,
    *,
    package_dir: Path,
    dll_path: Path,
    adb_path: Path,
    address: str,
    copilot_path: Path | None,
    label: str,
    metrics: RoundMetrics,
) -> tuple[dict[str, Any], list[str]]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    screenshot_path = store.run_dir / "snapshots" / f"{stamp}-{label}.png"
    screenshot = _capture_screenshot(
        adb_path=adb_path,
        address=address,
        destination=screenshot_path,
        metrics=metrics,
    )
    latest = store.run_dir / "snapshots" / "latest.png"
    latest.write_bytes(screenshot_path.read_bytes())
    status = read_json(store.status_path) or {}
    events, _ = store.read_events(limit=50)
    payload = {
        "captured_at": utc_now(),
        "label": label,
        "screenshot": screenshot,
        "current_task": {
            "status": status.get("status", "UNKNOWN"),
            "stage": status.get("stage"),
            "copilot": status.get("copilot"),
        },
        "formation": _formation_from_copilot(copilot_path),
        "callbacks": events,
        "versions": {
            "maa_core": _maa_version(dll_path),
            "maa_core_file": _file_version(dll_path),
            "maa_exe_file": _file_version(package_dir / "MAA.exe"),
            "resource": _resource_version(package_dir),
        },
    }
    snapshot_path = store.run_dir / "snapshots" / f"{stamp}.json"
    write_json_atomic(snapshot_path, payload)
    artifacts = [
        str(screenshot_path.resolve()),
        str(latest.resolve()),
        str(snapshot_path.resolve()),
    ]
    return payload, artifacts


def _snapshot_image(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the captured image metadata from a snapshot payload."""

    image = payload.get("screenshot")
    if not isinstance(image, dict) or not isinstance(image.get("path"), str):
        raise EvidenceError("snapshot payload has no captured screenshot")
    return image


def _open_snapshot_store(
    evidence_root: Path,
    run_id: str | None,
    attempt_id: str | None,
) -> RunStore:
    if run_id:
        try:
            return find_run_store(evidence_root, run_id=run_id)
        except EvidenceError:
            return RunStore(
                evidence_root,
                run_id=run_id,
                attempt_id=attempt_id,
            )
    active_path = evidence_root / ACTIVE_RUN_FILE
    active = read_json(active_path)
    if active and active.get("run_id"):
        try:
            return find_run_store(evidence_root, run_id=str(active["run_id"]))
        except EvidenceError:
            pass
    return RunStore(
        evidence_root,
        run_id=run_id or new_run_id(),
        attempt_id=attempt_id,
    )


def _snapshot(arguments: argparse.Namespace) -> tuple[dict[str, Any], int]:
    package_dir = _resolve(arguments.package_dir, _default_package_dir())
    evidence_root = _resolve(arguments.evidence_root, _default_evidence_root())
    adb_path = _resolve(arguments.adb, Path(arguments.adb)) if arguments.adb else (
        _default_adb()
    )
    if adb_path is None or not adb_path.is_file():
        raise EvidenceError("ADB executable was not found")
    store = _open_snapshot_store(
        evidence_root,
        arguments.run_id,
        arguments.attempt_id,
    )
    metrics = RoundMetrics(
        round_name="localai-debug-snapshot",
        stage=str(arguments.stage or "snapshot"),
        limits=BudgetLimits(tool_calls=20, screenshots=1),
    )
    copilot_path = (
        Path(arguments.copilot).expanduser().resolve()
        if arguments.copilot
        else None
    )
    payload, artifacts = _snapshot_payload(
        store,
        package_dir=package_dir,
        dll_path=package_dir / "MaaCore.dll",
        adb_path=adb_path,
        address=arguments.address,
        copilot_path=copilot_path,
        label=arguments.label,
        metrics=metrics,
    )
    previous = read_json(store.status_path)
    current_status = (
        str(previous.get("status"))
        if previous and previous.get("status") in ACTIVE_STATUSES
        else "SNAPSHOT_ONLY"
    )
    store.update_status(
        current_status,
        stage=arguments.stage or (previous or {}).get("stage"),
        last_snapshot_at=payload["captured_at"],
        last_snapshot=payload["screenshot"]["path"],
    )
    store.append_event(
        "snapshot_saved",
        {
            "label": arguments.label,
            "screenshot": payload["screenshot"],
        },
    )
    metrics.finish()
    metrics_path = metrics.write(store.run_dir / "snapshot-metrics.json")
    artifacts.extend([str(store.status_path), str(metrics_path)])
    return (
        result_envelope(
            "snapshot",
            "OK",
            run_id=store.run_id,
            attempt_id=store.attempt_id,
            data=payload,
            artifacts=artifacts,
        ),
        0,
    )


def _write_maa_event(store: RunStore, event: MaaEvent) -> None:
    store.append_event(
        "maa_callback",
        {
            "message_id": event.message_id,
            "message_name": event.message_name,
            "task_id": event.task_id,
            "task_chain": event.task_chain,
            "details": event.details,
        },
    )
    if event.message_name == "TASK_CHAIN_START":
        store.update_status(
            RunPhase.RUNNING,
            task_id=event.task_id,
            task_chain=event.task_chain,
        )
    elif event.message_name == "TASK_CHAIN_COMPLETED":
        store.update_status(
            RunPhase.VERIFYING,
            task_id=event.task_id,
            task_chain=event.task_chain,
        )
    elif event.message_name == "TASK_CHAIN_ERROR":
        store.update_status(
            RunPhase.FAILED,
            task_id=event.task_id,
            task_chain=event.task_chain,
            task_error=event.details,
        )
    elif event.message_name == "TASK_CHAIN_STOPPED":
        store.update_status(
            RunPhase.STOPPED,
            task_id=event.task_id,
            task_chain=event.task_chain,
        )
    elif event.fatal_connection:
        store.update_status(
            RunPhase.FAILED,
            connection_error=event.details,
        )


def _run_stage(
    arguments: argparse.Namespace | StageExecutionConfig,
) -> tuple[dict[str, Any], int]:
    config = (
        arguments
        if isinstance(arguments, StageExecutionConfig)
        else StageExecutionConfig.from_arguments(arguments)
    )
    config.validate()
    arguments = config.to_namespace()
    formal_battle_started = False
    if not arguments.confirm_formal_battle:
        raise EvidenceError(
            "run-stage requires --confirm-formal-battle after reviewing "
            "the selected stage and Copilot job"
        )
    package_dir = _resolve(arguments.package_dir, _default_package_dir())
    evidence_root = _resolve(arguments.evidence_root, _default_evidence_root())
    dll_path = _resolve(arguments.dll, package_dir / "MaaCore.dll")
    resource_dir = _resolve(arguments.resource_dir, package_dir / "resource")
    user_dir = _resolve(arguments.user_dir, package_dir)
    copilot_path = Path(arguments.copilot).expanduser().resolve()
    template_root = _resolve(
        arguments.template_root,
        package_dir / "resource" / "template" / "Battle",
    )
    state_memory_dir = _resolve(
        arguments.state_memory_dir,
        package_dir / "data" / "autopush" / "state-memory",
    )
    adb_path = _resolve(arguments.adb, Path(arguments.adb)) if arguments.adb else (
        _default_adb()
    )
    if adb_path is None or not adb_path.is_file():
        raise EvidenceError("ADB executable was not found")
    if not copilot_path.is_file():
        raise EvidenceError(f"Copilot job not found: {copilot_path}")
    if not dll_path.is_file():
        raise EvidenceError(f"MaaCore DLL not found: {dll_path}")
    if _maa_processes():
        raise EvidenceError(
            "MAA.exe is already running; close it before using run-stage"
        )

    metrics = RoundMetrics(
        round_name="localai-debug-run-stage",
        stage=arguments.stage,
        limits=BudgetLimits(
            screenshots=16,
            tool_calls=240,
            strategy_runs=1,
        ),
    )

    ensure_no_active_run(evidence_root)
    store = RunStore(evidence_root)
    store.mark_active()
    artifacts: list[str] = [
        str(store.manifest_path),
        str(store.status_path),
        str(store.events_path),
    ]
    try:
        store.create_manifest(
            {
                "command": "run-stage",
                "stage": arguments.stage,
                "address": arguments.address,
                "adb": str(adb_path),
                "dll": str(dll_path),
                "resource_dir": str(resource_dir),
                "user_dir": str(user_dir),
                "copilot": str(copilot_path),
                "state_memory_dir": str(state_memory_dir),
                "formation_enabled": arguments.formation,
                "ignore_requirements": arguments.ignore_requirements,
                "operbox_data_path": arguments.operbox_data,
                "formation": _formation_from_copilot(copilot_path),
                "versions": {
                    "maa_core": _maa_version(dll_path),
                    "maa_core_file": _file_version(dll_path),
                    "maa_exe_file": _file_version(package_dir / "MAA.exe"),
                    "resource": _resource_version(package_dir),
                },
                "resource_policy": {
                    "max_formal_attempts": 1,
                    "sanity_potions": 0,
                    "originite_prime": 0,
                },
            }
        )
        store.update_status(
            RunPhase.PREPARING,
            stage=arguments.stage,
            copilot=str(copilot_path),
            safe_exit_verified=False,
        )
        try:
            _validate_stage_job(copilot_path, arguments.stage)
        except (EvidenceError, OSError, ValueError) as exception:
            metrics.add_test(
                TestResult(
                    name="stage-job-binding",
                    success=False,
                    detail=str(exception),
                    strategy_path=str(copilot_path),
                )
            )
            metrics.finish()
            metrics_path = metrics.write(store.run_dir / "metrics.json")
            artifacts.append(str(metrics_path))
            store.update_status(
                RunPhase.UNKNOWN,
                completed_at=utc_now(),
                outcome="BLOCKED_STAGE_MISMATCH",
                error=str(exception),
                safe_exit_verified=False,
            )
            store.append_event(
                "stage_binding_blocked",
                {
                    "stage": arguments.stage,
                    "copilot": str(copilot_path),
                    "error": str(exception),
                },
            )
            return (
                result_envelope(
                    "run-stage",
                    "BLOCKED",
                    run_id=store.run_id,
                    attempt_id=store.attempt_id,
                    data={
                        "stage": arguments.stage,
                        "copilot": str(copilot_path),
                        "outcome": "BLOCKED_STAGE_MISMATCH",
                        "formal_battle_started": False,
                    },
                    errors=[str(exception)],
                    artifacts=artifacts,
                ),
                2,
            )
    except BaseException:
        store.clear_active()
        raise
    device_lease = DeviceControlLease(
        arguments.address,
        timeout_seconds=0,
    )
    try:
        device_lease.acquire()
        devices = AdbClient(
            adb_path=adb_path,
            serial=arguments.address,
            metrics=metrics,
        ).devices()
        if arguments.address not in devices:
            raise EvidenceError(
                f"ADB address is not ready: {arguments.address}; "
                f"ready devices={devices}"
            )
        screenshot, screenshot_artifacts = _snapshot_payload(
            store,
            package_dir=package_dir,
            dll_path=dll_path,
            adb_path=adb_path,
            address=arguments.address,
            copilot_path=copilot_path,
            label="preflight",
            metrics=metrics,
        )
        artifacts.extend(screenshot_artifacts)
        store.append_event("preflight_completed", {"screenshot": screenshot})
        preflight_image_payload = _snapshot_image(screenshot)
        try:
            memory = VisualStateMemory.load(state_memory_dir, metrics=metrics)
            preflight_image = cv2.imread(
                preflight_image_payload["path"],
                cv2.IMREAD_COLOR,
            )
            if preflight_image is None:
                raise ValueError("unable to decode preflight screenshot")
            preflight_state = memory.recognize(preflight_image, metrics=metrics)
        except (OSError, ValueError) as exception:
            store.update_status(
                RunPhase.UNKNOWN,
                completed_at=utc_now(),
                outcome="BLOCKED_PREFLIGHT_STATE",
                preflight_error=str(exception),
                safe_exit_verified=False,
            )
            store.append_event(
                "preflight_blocked",
                {"error": str(exception)},
            )
            return (
                result_envelope(
                    "run-stage",
                    "BLOCKED",
                    run_id=store.run_id,
                    attempt_id=store.attempt_id,
                    data={
                        "stage": arguments.stage,
                        "preflight_screenshot": screenshot,
                        "reason": (
                            "formal battle was not started because the current "
                            "game state could not be confirmed"
                        ),
                    },
                    errors=[f"preflight state: {exception}"],
                    artifacts=artifacts,
                ),
                2,
            )
        preflight_payload = {
            "name": preflight_state.name,
            "known": preflight_state.known,
            "distance": preflight_state.distance,
            "confidence": preflight_state.confidence,
            "reference": (
                str(preflight_state.reference_path)
                if preflight_state.reference_path
                else None
            ),
        }
        store.append_event("preflight_state_recognized", preflight_payload)
        preflight_safe = (
            preflight_state.known
            and _is_safe_preflight_state(
                preflight_state.name,
                arguments.stage,
            )
        )
        store.append_step(
            name="preflight",
            preconditions={
                "device_exclusive": True,
                "maa_process_absent": True,
                "stage_job_exact": True,
            },
            action="capture and recognize the current game page",
            postconditions={
                "state_known": preflight_state.known,
                "safe_to_start": preflight_safe,
            },
            timeout_seconds=10,
            evidence={
                "screenshot": preflight_image_payload,
                "state": preflight_payload,
            },
            failure=(
                None
                if preflight_safe
                else f"preflight state rejected: {preflight_state.name}"
            ),
        )
        if (
            not preflight_state.known
            or not _is_safe_preflight_state(
                preflight_state.name,
                arguments.stage,
            )
        ):
            store.update_status(
                RunPhase.UNKNOWN,
                completed_at=utc_now(),
                outcome="BLOCKED_PREFLIGHT_STATE",
                preflight_state=preflight_payload,
                safe_exit_verified=False,
            )
            return (
                result_envelope(
                    "run-stage",
                    "BLOCKED",
                    run_id=store.run_id,
                    attempt_id=store.attempt_id,
                    data={
                        "stage": arguments.stage,
                        "preflight_screenshot": preflight_image_payload,
                        "preflight_state": preflight_payload,
                        "reason": (
                            "formal battle was not started because the current "
                            "state is unknown or not in the safe preflight set"
                        ),
                    },
                    errors=[
                        f"preflight state rejected: {preflight_state.name}"
                    ],
                    artifacts=artifacts,
                ),
                2,
            )
        store.update_status(RunPhase.RUNNING, safe_exit_verified=False)
        formal_battle_started = True
        verifier = BattleResultVerifier(
            template_root,
            threshold=arguments.verify_threshold,
        )
        settlement_capture: dict[str, Any] = {}
        settlement_lock = threading.Lock()

        def capture_core_settlement(event: MaaEvent) -> None:
            _write_maa_event(store, event)
            if not event_has_three_star_result(event):
                return
            with settlement_lock:
                if settlement_capture.get("attempted"):
                    return
                settlement_capture["attempted"] = True
                destination = (
                    store.run_dir / "artifacts" / "settlement-at-core-star.png"
                )
                try:
                    screenshot, captured_verification = _capture_single_result(
                        adb_path=adb_path,
                        address=arguments.address,
                        destination=destination,
                        metrics=metrics,
                        verifier=verifier,
                    )
                except (AdbError, OSError, ValueError) as exception:
                    settlement_capture["error"] = str(exception)
                    store.append_step(
                        name="settlement-capture",
                        preconditions={
                            "maa_core_three_star_callback": True,
                            "capture_slot_available": True,
                        },
                        action=(
                            "capture and recognize settlement before Core "
                            "leaves the result screen"
                        ),
                        postconditions={
                            "screenshot_saved": False,
                            "recognition_status": "unknown",
                        },
                        timeout_seconds=0,
                        evidence={"timing": "maa_core_three_star_callback"},
                        failure=str(exception),
                    )
                    store.append_event(
                        "settlement_capture_failed",
                        {
                            "timing": "maa_core_three_star_callback",
                            "error": str(exception),
                        },
                    )
                    return
                settlement_capture.update(
                    {
                        "screenshot": screenshot,
                        "verification": captured_verification,
                    }
                )
                evidence_path = (
                    store.run_dir
                    / "artifacts"
                    / "settlement-at-core-star-verification.json"
                )
                write_json_atomic(
                    evidence_path,
                    {
                        "schema_version": "localai.debug/v1",
                        "run_id": store.run_id,
                        "attempt_id": store.attempt_id,
                        "timing": "maa_core_three_star_callback",
                        "screenshot": screenshot,
                        "verification": asdict(captured_verification),
                    },
                )
                store.append_step(
                    name="settlement-capture",
                    preconditions={
                        "maa_core_three_star_callback": True,
                        "capture_slot_available": True,
                    },
                    action=(
                        "capture and recognize settlement before Core leaves "
                        "the result screen"
                    ),
                    postconditions={
                        "screenshot_saved": True,
                        "recognition_status": captured_verification.status,
                    },
                    timeout_seconds=0,
                    evidence={
                        "timing": "maa_core_three_star_callback",
                        "screenshot": screenshot,
                        "verification_path": str(evidence_path.resolve()),
                    },
                )
                store.append_event(
                    "settlement_captured",
                    {
                        "timing": "maa_core_three_star_callback",
                        "screenshot": screenshot,
                        "verification": asdict(captured_verification),
                        "evidence_path": str(evidence_path.resolve()),
                    },
                )

        events = run_copilot_file(
            dll_path=dll_path,
            resource_dir=resource_dir,
            user_dir=user_dir,
            adb_path=adb_path,
            address=arguments.address,
            connect_config=arguments.connect_config,
            copilot_file=copilot_path,
            metrics=metrics,
            timeout_seconds=arguments.timeout,
            exercise=False,
            formation=arguments.formation,
            formation_index=arguments.formation_index,
            add_trust=arguments.add_trust,
            ignore_requirements=arguments.ignore_requirements,
            operbox_data_path=arguments.operbox_data,
            support_unit_usage=arguments.support_unit_usage,
            wait_until_end=True,
            is_raid=arguments.is_raid,
            event_sink=capture_core_settlement,
            stop_requested=store.stop_requested,
        )
        summary = summarize_events(events)
        result_path = store.run_dir / "artifacts" / "result.png"
        verification = settlement_capture.get("verification")
        capture_error: str | None = settlement_capture.get("error")
        result_image = settlement_capture.get("screenshot")
        if result_image is not None:
            artifacts.extend(
                [
                    str(result_image["path"]),
                    str(
                        store.run_dir
                        / "artifacts"
                        / "settlement-at-core-star-verification.json"
                    ),
                ]
            )
        stop_requested = store.stop_requested()
        safe_exit_verified = False
        post_stop_payload: dict[str, Any] | None = None
        if stop_requested or summary["terminal_state"] == "STOPPED":
            try:
                post_stop_payload, post_stop_artifacts = _snapshot_payload(
                    store,
                    package_dir=package_dir,
                    dll_path=dll_path,
                    adb_path=adb_path,
                    address=arguments.address,
                    copilot_path=copilot_path,
                    label="after-stop",
                    metrics=metrics,
                )
                artifacts.extend(post_stop_artifacts)
                post_stop_image = _snapshot_image(post_stop_payload)
                decoded_post_stop = cv2.imread(
                    post_stop_image["path"],
                    cv2.IMREAD_COLOR,
                )
                if decoded_post_stop is None:
                    raise ValueError("unable to decode post-stop screenshot")
                post_stop_state = memory.recognize(
                    decoded_post_stop,
                    metrics=metrics,
                )
                safe_exit_verified = (
                    post_stop_state.known
                    and _is_safe_post_stop_state(
                        post_stop_state.name,
                        arguments.stage,
                    )
                )
                post_stop_payload["state"] = {
                    "name": post_stop_state.name,
                    "known": post_stop_state.known,
                    "distance": post_stop_state.distance,
                    "confidence": post_stop_state.confidence,
                    "safe_exit_verified": safe_exit_verified,
                }
                store.append_step(
                    name="post-stop-safe-exit",
                    preconditions={
                        "stop_requested": stop_requested,
                        "core_terminal_state": summary["terminal_state"],
                    },
                    action="capture and recognize the page after Core stops",
                    postconditions={
                        "state": post_stop_state.name,
                        "safe_exit_verified": safe_exit_verified,
                    },
                    timeout_seconds=10,
                    evidence=post_stop_payload,
                    failure=(
                        None
                        if safe_exit_verified
                        else "post-stop page is not in the safe state set"
                    ),
                )
            except (AdbError, OSError, ValueError) as exception:
                store.append_step(
                    name="post-stop-safe-exit",
                    preconditions={
                        "stop_requested": stop_requested,
                        "core_terminal_state": summary["terminal_state"],
                    },
                    action="capture and recognize the page after Core stops",
                    postconditions={
                        "state": "unknown",
                        "safe_exit_verified": False,
                    },
                    timeout_seconds=10,
                    evidence={},
                    failure=str(exception),
                )
        can_verify = (
            not stop_requested
            and summary["terminal_state"] == "COMPLETED"
            and not summary["task_errors"]
            and not summary["connection_errors"]
        )
        store.append_step(
            name="formal-battle",
            preconditions={
                "preflight_safe": True,
                "stage_job_exact": True,
                "sanity_potions_disabled": True,
                "originite_prime_disabled": True,
            },
            action="execute one formal MaaCore Copilot clear",
            postconditions={
                "terminal_state": summary["terminal_state"],
                "verification_required": True,
                "result_verified": False,
            },
            timeout_seconds=arguments.timeout,
            evidence={
                "task": summary,
                "copilot": str(copilot_path),
                "stop_requested": stop_requested,
            },
            failure=(
                None
                if summary["terminal_state"] == "COMPLETED"
                else summary["terminal_state"]
            ),
        )
        if can_verify:
            store.update_status(RunPhase.VERIFYING, task=summary)
            if verification is None:
                try:
                    verification, result_screenshots = _capture_verified_result(
                        adb_path=adb_path,
                        address=arguments.address,
                        destination=result_path,
                        metrics=metrics,
                        verifier=verifier,
                    )
                    result_image = result_screenshots[-1]
                    artifacts.extend(
                        item["path"] for item in result_screenshots
                    )
                except (AdbError, OSError, ValueError) as exception:
                    capture_error = str(exception)
                    result_image = None
        elif summary["terminal_state"] == "STOPPED" or stop_requested:
            store.update_status(RunPhase.STOPPING, task=summary)
        elif summary["terminal_state"] == "DISCONNECTED":
            store.update_status(RunPhase.FAILED, task=summary)
        elif summary["task_errors"]:
            store.update_status(RunPhase.FAILED, task=summary)
        else:
            store.update_status(RunPhase.UNKNOWN, task=summary)

        core_star_evidence = summary.get("maa_three_star_evidence") or []
        core_three_star = bool(summary.get("maa_three_star"))
        resolution = resolve_battle_evidence(
            verification,
            core_three_star=core_three_star,
            core_evidence=core_star_evidence,
        )
        verification_status = resolution.status
        outcome = classify_outcome(
            stop_requested=stop_requested,
            task_completed=bool(summary["task_completed"]),
            task_errors=list(summary["task_errors"]),
            connection_errors=list(summary["connection_errors"]),
            terminal_state=str(summary["terminal_state"]),
            verification_status=verification_status,
        )
        store.append_step(
            name="evidence-decision",
            preconditions={
                "task_terminal_state": summary["terminal_state"],
                "screenshot_present": verification is not None,
                "maa_core_star_present": core_three_star,
            },
            action=(
                "reconcile screenshot and MaaCore evidence without promoting "
                "conflicting results"
            ),
            postconditions={
                "status": resolution.status,
                "conflict": resolution.conflict,
                "run_phase": outcome.value,
            },
            timeout_seconds=0,
            evidence={
                "resolution": asdict(resolution),
                "visual": (
                    asdict(verification) if verification is not None else None
                ),
                "maa_core": core_star_evidence,
            },
            failure=resolution.reason if resolution.conflict else None,
        )
        verification_payload: dict[str, Any] = {
            "status": resolution.status,
            "stars": resolution.stars,
            "confidence": resolution.confidence,
            "needs_computer_use": (
                verification.needs_computer_use
                if verification is not None
                else False
            ),
            "evidence": (
                list(asdict(verification)["evidence"])
                if verification is not None
                else []
            ),
            "source": "+".join(resolution.sources) or None,
            "conflict": resolution.conflict,
            "reason": resolution.reason,
            "visual_evidence": (
                asdict(verification) if verification is not None else None
            ),
            "maa_core_evidence": core_star_evidence,
        }
        verification_path = (
            store.run_dir / "artifacts" / "result-verification.json"
        )
        write_json_atomic(
            verification_path,
            {
                "schema_version": "localai.debug/v1",
                "run_id": store.run_id,
                "attempt_id": store.attempt_id,
                "verification": verification_payload,
                "resolution": asdict(resolution),
            },
        )
        artifacts.append(str(verification_path))
        metrics.add_test(
            TestResult(
                name="formal-run-outcome",
                success=outcome is RunPhase.SUCCEEDED,
                detail=outcome.value,
                strategy_path=str(copilot_path),
                stars=resolution.stars,
            )
        )
        metrics.finish()
        metrics_path = metrics.write(store.run_dir / "metrics.json")
        artifacts.append(str(metrics_path))
        data = {
            "stage": arguments.stage,
            "outcome": outcome.value,
            "task": summary,
            "verification": verification_payload,
            "verification_status": verification_status,
            "verification_conflict": resolution.conflict,
            "verification_reason": resolution.reason,
            "formal_battle_started": formal_battle_started,
            "capture_error": capture_error,
            "result_screenshot": result_image,
            "stop_requested": stop_requested,
            "stop_request": store.read_stop_request(),
            "preflight_state": preflight_payload,
            "post_stop_state": post_stop_payload,
            "safe_exit_verified": safe_exit_verified,
            "resource_policy": {
                "sanity_potions": 0,
                "originite_prime": 0,
                "attempts": 1,
            },
        }
        store.update_status(
            outcome,
            outcome=outcome.value,
            completed_at=utc_now(),
            task=summary,
            verification=verification_payload,
            preflight_state=preflight_payload,
            result_screenshot=(
                result_image.get("path")
                if isinstance(result_image, dict)
                else str(result_path.resolve())
                if result_path.is_file()
                else None
            ),
            safe_exit_verified=safe_exit_verified,
            post_stop_state=post_stop_payload,
        )
        store.append_event("run_finished", data)
        exit_code = 0 if outcome is RunPhase.SUCCEEDED else 2
        return (
            result_envelope(
                "run-stage",
                outcome,
                run_id=store.run_id,
                attempt_id=store.attempt_id,
                data=data,
                artifacts=artifacts,
            ),
            exit_code,
        )
    except Exception as exception:
        metrics.add_test(
            TestResult(
                name="formal-run-runtime",
                success=False,
                detail=str(exception),
                strategy_path=str(copilot_path),
            )
        )
        metrics.finish()
        metrics_path = metrics.write(store.run_dir / "metrics.json")
        store.update_status(
            RunPhase.FAILED,
            completed_at=utc_now(),
            outcome="FAILED_RUNTIME",
            errors=[str(exception)],
            safe_exit_verified=False,
        )
        store.append_event(
            "run_runtime_error",
            {"error_type": type(exception).__name__, "error": str(exception)},
        )
        return (
            result_envelope(
                "run-stage",
                RunPhase.FAILED,
                run_id=store.run_id,
                attempt_id=store.attempt_id,
                data={
                    "stage": arguments.stage,
                    "outcome": "FAILED_RUNTIME",
                    "formal_battle_started": formal_battle_started,
                },
                errors=[f"{type(exception).__name__}: {exception}"],
                artifacts=[*artifacts, str(metrics_path)],
            ),
            2,
        )
    finally:
        try:
            device_lease.release()
        finally:
            store.clear_active()


def _status(arguments: argparse.Namespace) -> tuple[dict[str, Any], int]:
    evidence_root = _resolve(arguments.evidence_root, _default_evidence_root())
    store = find_run_store(evidence_root, run_id=arguments.run_id)
    status = read_json(store.status_path)
    manifest = read_json(store.manifest_path)
    if status is None and manifest is None:
        raise EvidenceError(f"run has no status or manifest: {store.run_id}")
    return (
        result_envelope(
            "status",
            "OK",
            run_id=store.run_id,
            attempt_id=store.attempt_id,
            data={
                "run_dir": str(store.run_dir),
                "status": status,
                "manifest": manifest,
            },
            artifacts=[
                path
                for path in (str(store.status_path), str(store.manifest_path))
                if Path(path).is_file()
            ],
        ),
        0,
    )


def _events(arguments: argparse.Namespace) -> tuple[dict[str, Any], int]:
    evidence_root = _resolve(arguments.evidence_root, _default_evidence_root())
    store = find_run_store(evidence_root, run_id=arguments.run_id)
    events, next_after = store.read_events(
        after=arguments.after,
        limit=arguments.limit,
    )
    return (
        result_envelope(
            "events",
            "OK",
            run_id=store.run_id,
            attempt_id=store.attempt_id,
            data={
                "events": events,
                "after": arguments.after,
                "next_after": next_after,
                "returned": len(events),
            },
            artifacts=[str(store.events_path)] if store.events_path.is_file() else [],
        ),
        0,
    )


def _campaign_status(
    arguments: argparse.Namespace,
) -> tuple[dict[str, Any], int]:
    evidence_root = _resolve(arguments.evidence_root, _default_evidence_root())
    store = CampaignStore(evidence_root, arguments.campaign_id)
    state = store.load()
    return (
        result_envelope(
            "campaign-status",
            state.status,
            data={
                "campaign": state.to_dict(),
                "current_stage": state.current_stage,
                "attempts": [asdict(item) for item in state.attempts],
                "sanity_spent": state.sanity_spent,
            },
            artifacts=[
                str(path)
                for path in (store.state_path, store.events_path)
                if path.is_file()
            ],
        ),
        0,
    )


def _stop(arguments: argparse.Namespace) -> tuple[dict[str, Any], int]:
    package_dir = _resolve(arguments.package_dir, _default_package_dir())
    evidence_root = _resolve(arguments.evidence_root, _default_evidence_root())
    store = find_run_store(evidence_root, run_id=arguments.run_id)
    status = read_json(store.status_path) or {}
    if status.get("status") not in ACTIVE_STATUSES:
        raise EvidenceError(
            f"run is not active: {status.get('status', 'UNKNOWN')}"
        )
    # Persist the cooperative stop before taking a diagnostic screenshot. The
    # active runner owns the device mutex, so screenshot acquisition may fail
    # and must not prevent the stop request from reaching it.
    request = store.request_stop(
        reason=arguments.reason,
        requested_by=arguments.requested_by,
    )
    adb_path = _resolve(arguments.adb, Path(arguments.adb)) if arguments.adb else (
        _default_adb()
    )
    snapshot: dict[str, Any] | None = None
    snapshot_error: str | None = None
    if adb_path and adb_path.is_file():
        metrics = RoundMetrics(
            round_name="localai-debug-stop",
            stage=str(status.get("stage") or "stop"),
            limits=BudgetLimits(tool_calls=20, screenshots=1),
        )
        try:
            snapshot, artifacts = _snapshot_payload(
                store,
                package_dir=package_dir,
                dll_path=package_dir / "MaaCore.dll",
                adb_path=adb_path,
                address=arguments.address,
                copilot_path=(
                    Path(str(status["copilot"]))
                    if status.get("copilot")
                    else None
                ),
                label="before-stop",
                metrics=metrics,
            )
        except (AdbError, DeviceBusyError, OSError, ValueError) as exception:
            snapshot_error = str(exception)
            artifacts = []
    else:
        artifacts = []
        snapshot_error = "ADB executable was not found"
    active = read_json(store.active_run_path) or {}
    active_pid = active.get("pid")
    runner_alive = isinstance(active_pid, int) and process_is_running(active_pid)
    run_status = RunPhase.STOPPING if runner_alive else RunPhase.STOPPED
    store.update_status(
        run_status,
        stop_request=request,
        safe_exit_verified=False,
        stop_reason=(
            "stop delivered to active runner"
            if runner_alive
            else "runner process was no longer active"
        ),
    )
    if not runner_alive:
        store.clear_active()
    return (
        result_envelope(
            "stop",
            "STOPPING_UNVERIFIED" if runner_alive else "STOPPED_UNVERIFIED",
            run_id=store.run_id,
            attempt_id=store.attempt_id,
            data={
                "request": request,
                "snapshot": snapshot,
                "snapshot_error": snapshot_error,
                "safe_exit_verified": False,
                "runner_alive": runner_alive,
                "note": (
                    "MaaCore will receive AsstStop on the next polling interval; "
                    "battle exit has not been verified"
                ),
            },
            artifacts=artifacts,
        ),
        0,
    )


def _replay(arguments: argparse.Namespace) -> tuple[dict[str, Any], int]:
    package_dir = _resolve(arguments.package_dir, _default_package_dir())
    evidence_root = _resolve(arguments.evidence_root, _default_evidence_root())
    store = find_run_store(evidence_root, run_id=arguments.run_id)
    status = read_json(store.status_path) or {}
    image_path = (
        Path(arguments.image).expanduser().resolve()
        if arguments.image
        else store.run_dir / "artifacts" / "result.png"
    )
    if not image_path.is_file():
        raise EvidenceError(f"replay screenshot not found: {image_path}")
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise EvidenceError(f"unable to decode replay screenshot: {image_path}")
    template_root = _resolve(
        arguments.template_root,
        package_dir / "resource" / "template" / "Battle",
    )
    metrics = RoundMetrics(
        round_name="localai-debug-replay",
        stage=str(status.get("stage") or "replay"),
        limits=BudgetLimits(tool_calls=20, screenshots=0),
    )
    verification = BattleResultVerifier(
        template_root,
        threshold=arguments.threshold,
    ).verify(image, metrics=metrics)
    events, _ = store.read_events(limit=100_000)
    callback_events = [
        MaaEvent(
            message_id=int(event["data"].get("message_id", 0)),
            details=dict(event["data"].get("details") or {}),
        )
        for event in events
        if event.get("type") == "maa_callback"
        and isinstance(event.get("data"), dict)
    ]
    callback_summary = summarize_events(callback_events)
    transition_trace = []
    for index, event in enumerate(callback_events, start=1):
        summary = summarize_events(callback_events[:index])
        transition_trace.append(
            {
                "event_index": index,
                "message_id": event.message_id,
                "message_name": event.message_name,
                "task_id": event.task_id,
                "terminal_state": summary["terminal_state"],
                "phase": summary["phase"],
            }
        )
    evidence_resolution = resolve_battle_evidence(
        verification,
        core_three_star=bool(callback_summary["maa_three_star"]),
        core_evidence=callback_summary["maa_three_star_evidence"],
    )
    previous = status.get("verification")
    replay = {
        "image": str(image_path),
        "verification": asdict(verification),
        "callback_summary": callback_summary,
        "transition_trace": transition_trace,
        "evidence_resolution": asdict(evidence_resolution),
        "previous_verification": previous,
        "matches_previous": (
            isinstance(previous, dict)
            and previous.get("status") == evidence_resolution.status
            and previous.get("stars") == evidence_resolution.stars
        ),
        "scope": (
            "saved result recognition, callback state transitions, and "
            "evidence reconciliation; live device replay is not implemented"
        ),
    }
    replay_path = store.run_dir / "artifacts" / "replay.json"
    write_json_atomic(
        replay_path,
        {
            "schema_version": "localai.debug/v1",
            "run_id": store.run_id,
            "attempt_id": store.attempt_id,
            "replayed_at": utc_now(),
            **replay,
        },
    )
    store.append_event("replay_completed", replay)
    metrics.finish()
    metrics_path = metrics.write(store.run_dir / "artifacts" / "replay-metrics.json")
    return (
        result_envelope(
            "replay",
            "OK",
            run_id=store.run_id,
            attempt_id=store.attempt_id,
            data=replay,
            artifacts=[str(replay_path), str(metrics_path), str(image_path)],
        ),
        0 if verification.status == "three_star" else 2,
    )


def _recognition_check(
    arguments: argparse.Namespace,
) -> tuple[dict[str, Any], int]:
    """Evaluate an external corpus of real, human-confirmed captures."""

    package_dir = _resolve(arguments.package_dir, _default_package_dir())
    template_root = _resolve(
        arguments.template_root,
        package_dir / "resource" / "template" / "Battle",
    )
    report = run_recognition_corpus(
        arguments.corpus,
        template_root=template_root,
        threshold=arguments.threshold,
    )
    payload = report_payload(report)
    artifacts: list[str] = []
    if arguments.report:
        report_path = Path(arguments.report).expanduser().resolve()
        write_json_atomic(
            report_path,
            {
                "schema_version": "localai.debug/v1",
                "command": "recognition-check",
                "timestamp": utc_now(),
                "data": payload,
            },
        )
        artifacts.append(str(report_path))
    return (
        result_envelope(
            "recognition-check",
            "OK" if report.passed else "UNVERIFIED",
            data=payload,
            artifacts=artifacts,
        ),
        0 if report.passed else 2,
    )


def _campaign_policy(arguments: argparse.Namespace) -> CampaignPolicy:
    return CampaignPolicy(
        max_attempts_per_stage=int(
            getattr(arguments, "max_attempts_per_stage", 3)
        ),
        max_total_attempts=int(
            getattr(arguments, "max_total_attempts", 6)
        ),
        max_elapsed_seconds=int(
            getattr(arguments, "max_elapsed_seconds", 3600)
        ),
        max_sanity=getattr(arguments, "max_sanity", None),
        sanity_costs=parse_sanity_costs(
            getattr(arguments, "sanity_cost", None)
        ),
    )


def _campaign_envelope(
    state: CampaignState,
    store: CampaignStore,
    *,
    status: str,
    errors: list[str] | None = None,
) -> tuple[dict[str, Any], int]:
    artifacts = [str(store.state_path)]
    if store.events_path.is_file():
        artifacts.append(str(store.events_path))
    artifacts.extend(
        str(store.evidence_root / str(attempt.run_id) / "manifest.json")
        for attempt in state.attempts
        if attempt.run_id
        and (
            store.evidence_root / str(attempt.run_id) / "manifest.json"
        ).is_file()
    )
    return (
        result_envelope(
            "run-campaign",
            status,
            data={
                "campaign": state.to_dict(),
                "current_stage": state.current_stage,
                "attempts": [asdict(item) for item in state.attempts],
                "sanity_spent": state.sanity_spent,
                "remaining_attempts": max(
                    0,
                    state.policy.max_total_attempts - len(state.attempts),
                ),
                "resource_policy": {
                    "sanity_potions": 0,
                    "originite_prime": 0,
                },
            },
            errors=errors or [],
            artifacts=artifacts,
        ),
        0 if status == "OK" else 2,
    )


def _run_campaign(
    arguments: argparse.Namespace,
) -> tuple[dict[str, Any], int]:
    """Run a persistent queue across stages and eligible candidates."""

    if not arguments.confirm_formal_battle:
        raise EvidenceError(
            "run-campaign requires --confirm-formal-battle after reviewing "
            "the stage list and job root"
        )
    package_dir = _resolve(arguments.package_dir, _default_package_dir())
    evidence_root = _resolve(arguments.evidence_root, _default_evidence_root())
    dll_path = _resolve(arguments.dll, package_dir / "MaaCore.dll")
    resource_dir = _resolve(arguments.resource_dir, package_dir / "resource")
    user_dir = _resolve(arguments.user_dir, package_dir)
    template_root = _resolve(
        arguments.template_root,
        package_dir / "resource" / "template" / "Battle",
    )
    state_memory_dir = _resolve(
        arguments.state_memory_dir,
        package_dir / "data" / "autopush" / "state-memory",
    )
    adb_path = _resolve(arguments.adb, Path(arguments.adb)) if arguments.adb else (
        _default_adb()
    )
    if adb_path is None or not adb_path.is_file():
        raise EvidenceError("ADB executable was not found")
    roster = load_roster(arguments.roster)
    jobs_root = Path(arguments.jobs_root).expanduser().resolve()
    if not jobs_root.is_dir():
        raise EvidenceError(f"jobs root is not a directory: {jobs_root}")

    stages = list(arguments.stages)
    missing_job_dirs = [
        str(jobs_root / stage)
        for stage in stages
        if not (jobs_root / stage).is_dir()
    ]
    if missing_job_dirs:
        raise EvidenceError(
            "campaign job directories are missing: "
            + ", ".join(missing_job_dirs)
        )
    policy = _campaign_policy(arguments)
    policy.validate(stages)
    campaign_id = str(
        getattr(arguments, "campaign_id", None) or CampaignStore.new_id()
    )
    store = CampaignStore(evidence_root, campaign_id)
    if store.state_path.is_file():
        state = store.load()
        if state.stages != stages or state.policy != policy:
            raise CampaignError(
                "resumed campaign stages or policy differ from persisted state"
            )
        if state.status == "SUCCEEDED":
            return _campaign_envelope(state, store, status="OK")
        if state.status in {"BLOCKED", "BUDGET_EXHAUSTED"}:
            return _campaign_envelope(
                state,
                store,
                status=state.status,
                errors=[state.pause_reason or "campaign cannot continue"],
            )
        if state.status == "PAUSED":
            if not getattr(arguments, "resume_campaign", False):
                return _campaign_envelope(
                    state,
                    store,
                    status="PAUSED",
                    errors=[state.pause_reason or "campaign is paused"],
                )
            state.resume(str(getattr(arguments, "recovery_evidence", "") or ""))
            store.append_event(
                "campaign_resumed",
                {"recovery_evidence": state.recovery_evidence},
            )
        elif not getattr(arguments, "resume_campaign", False):
            raise CampaignError(
                "existing active campaign requires --resume-campaign"
            )
        store.save(state)
    else:
        state = store.create(stages=stages, policy=policy)
        store.append_event(
            "campaign_started",
            {
                "stages": stages,
                "policy": asdict(policy),
            },
        )

    while state.stage_index < len(state.stages):
        stage = state.stages[state.stage_index]
        if state.status != "ACTIVE":
            return _campaign_envelope(
                state,
                store,
                status=state.status,
                errors=[state.pause_reason or "campaign stopped"],
            )
        if state.elapsed_seconds() >= policy.max_elapsed_seconds:
            state.status = "BUDGET_EXHAUSTED"
            state.pause_reason = "campaign time budget exhausted"
            store.save(state)
            return _campaign_envelope(
                state,
                store,
                status=state.status,
                errors=[state.pause_reason],
            )
        if len(state.attempts) >= policy.max_total_attempts:
            state.status = "BUDGET_EXHAUSTED"
            state.pause_reason = "campaign attempt budget exhausted"
            store.save(state)
            return _campaign_envelope(
                state,
                store,
                status=state.status,
                errors=[state.pause_reason],
            )
        if state.attempts_for_stage(stage) >= policy.max_attempts_per_stage:
            state.status = "BUDGET_EXHAUSTED"
            state.pause_reason = f"stage attempt budget exhausted: {stage}"
            store.save(state)
            return _campaign_envelope(
                state,
                store,
                status=state.status,
                errors=[state.pause_reason],
            )
        next_cost = policy.sanity_costs[stage]
        if (
            policy.max_sanity is not None
            and state.sanity_spent + next_cost > policy.max_sanity
        ):
            state.status = "BUDGET_EXHAUSTED"
            state.pause_reason = "campaign sanity budget exhausted"
            store.save(state)
            return _campaign_envelope(
                state,
                store,
                status=state.status,
                errors=[state.pause_reason],
            )
        jobs_dir = jobs_root / stage
        if not jobs_dir.is_dir():
            raise EvidenceError(f"no job directory for stage: {jobs_dir}")
        metrics = RoundMetrics(
            round_name=arguments.round_name,
            stage=stage,
            limits=BudgetLimits(
                screenshots=16,
                tool_calls=240,
                strategy_runs=1,
            ),
        )
        candidates = rank_strategies(
            collect_strategy_files([jobs_dir]),
            stage,
            roster,
            allow_support=False,
            metrics=metrics,
        )
        eligible = [
            candidate
            for candidate in candidates
            if candidate.requirements_satisfied
            and not candidate_has_hard_requirement_gap(candidate)
            and (
                not isinstance(getattr(candidate.strategy, "raw", None), dict)
                or not job_is_raid_only(candidate.strategy.raw)
            )
        ]
        if not eligible:
            state.status = "BLOCKED"
            state.pause_reason = (
                f"no runnable job satisfied hard requirements: {stage}"
            )
            store.save(state)
            store.append_event(
                "campaign_blocked",
                {"stage": stage, "reason": state.pause_reason},
            )
            return _campaign_envelope(
                state,
                store,
                status=state.status,
                errors=[state.pause_reason],
            )
        candidate_index = state.next_candidate_index.get(stage, 0)
        attempted_jobs = {
            attempt.job
            for attempt in state.attempts
            if attempt.stage == stage
        }
        while (
            candidate_index < len(eligible)
            and str(eligible[candidate_index].strategy.strategy_id)
            in attempted_jobs
        ):
            candidate_index += 1
        state.next_candidate_index[stage] = candidate_index
        if candidate_index >= len(eligible):
            state.status = "BUDGET_EXHAUSTED"
            state.pause_reason = f"all eligible jobs exhausted: {stage}"
            store.save(state)
            return _campaign_envelope(
                state,
                store,
                status=state.status,
                errors=[state.pause_reason],
            )
        selected = eligible[candidate_index]
        stage_config = StageExecutionConfig(
            stage=stage,
            copilot=str(selected.strategy.strategy_id),
            package_dir=str(package_dir),
            evidence_root=str(evidence_root),
            dll=str(dll_path),
            resource_dir=str(resource_dir),
            user_dir=str(user_dir),
            template_root=str(template_root),
            state_memory_dir=str(state_memory_dir),
            adb=str(adb_path),
            address=arguments.address,
            connect_config=arguments.connect_config,
            timeout=arguments.timeout,
            verify_threshold=float(
                getattr(
                    arguments,
                    "verify_threshold",
                    DEFAULT_VERIFY_THRESHOLD,
                )
            ),
            formation=True,
            formation_index=0,
            add_trust=False,
            ignore_requirements=False,
            operbox_data=str(arguments.operbox_data or arguments.roster),
            support_unit_usage=0,
            confirm_formal_battle=True,
        )
        stage_config.validate()
        state.next_candidate_index[stage] = candidate_index + 1
        store.save(state)
        store.append_event(
            "candidate_selected",
            {
                "stage": stage,
                "candidate_index": candidate_index,
                "job": str(selected.strategy.strategy_id),
                "lineup": [asdict(slot) for slot in selected.lineup],
            },
        )
        try:
            envelope, _ = _run_stage(stage_config)
        except Exception as exception:
            state.pause(
                f"campaign paused after unhandled runner error at stage="
                f"{stage}: {type(exception).__name__}: {exception}"
            )
            store.save(state)
            store.append_event(
                "campaign_paused",
                {"stage": stage, "reason": state.pause_reason},
            )
            return _campaign_envelope(
                state,
                store,
                status="PAUSED",
                errors=[state.pause_reason],
            )
        data = envelope.get("data") or {}
        battle_started = bool(data.get("formal_battle_started"))
        store.record_attempt(
            state,
            CampaignAttempt(
                stage=stage,
                job=str(selected.strategy.strategy_id),
                run_id=envelope.get("run_id"),
                attempt_id=envelope.get("attempt_id"),
                status=str(envelope.get("status")),
                outcome=data.get("outcome"),
                sanity_cost=next_cost if battle_started else 0,
                formal_battle_started=battle_started,
                finished_at=utc_now(),
            ),
        )
        store.append_event(
            "attempt_finished",
            asdict(state.attempts[-1]),
        )
        if envelope.get("status") == "SUCCEEDED":
            store.append_event(
                "stage_succeeded",
                {"stage": stage, "job": state.attempts[-1].job},
            )
            state.stage_index += 1
            state.next_candidate_index[stage] = 0
            store.save(state)
            continue
        if (
            envelope.get("status") == "FAILED"
            and battle_started
            and data.get("verification_status")
            in {"two_star", "non_three_star"}
        ):
            continue
        state.pause(
            "campaign paused after "
            f"stage={stage} status={envelope.get('status')} "
            f"outcome={data.get('outcome')}"
        )
        store.save(state)
        store.append_event(
            "campaign_paused",
            {"stage": stage, "reason": state.pause_reason},
        )
        return _campaign_envelope(
            state,
            store,
            status="PAUSED",
            errors=[state.pause_reason],
        )

    state.status = "SUCCEEDED"
    state.pause_reason = None
    store.save(state)
    store.append_event("campaign_succeeded", {"stages": state.stages})
    return _campaign_envelope(state, store, status="OK")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maa-autopush-debug",
        description="JSON-first LocalAI run and evidence CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument(
            "--package-dir",
            default=str(_default_package_dir()),
        )
        command_parser.add_argument(
            "--evidence-root",
            default=str(_default_evidence_root()),
        )
        command_parser.add_argument("--adb")
        command_parser.add_argument("--address", default="emulator-5554")

    doctor = subparsers.add_parser("doctor", help="check the local debug runtime")
    add_common(doctor)
    doctor.set_defaults(handler=_doctor)

    snapshot = subparsers.add_parser(
        "snapshot",
        help="capture a screenshot and a self-contained evidence record",
    )
    add_common(snapshot)
    snapshot.add_argument("--run-id")
    snapshot.add_argument("--attempt-id")
    snapshot.add_argument("--stage")
    snapshot.add_argument("--copilot")
    snapshot.add_argument("--label", default="manual")
    snapshot.set_defaults(handler=_snapshot)

    run_stage = subparsers.add_parser(
        "run-stage",
        help="execute one formal Copilot clear and verify the result",
    )
    add_common(run_stage)
    run_stage.add_argument("--stage", required=True)
    run_stage.add_argument("--copilot", required=True)
    run_stage.add_argument("--dll")
    run_stage.add_argument("--resource-dir")
    run_stage.add_argument("--user-dir")
    run_stage.add_argument("--connect-config", default="General")
    run_stage.add_argument("--timeout", type=int, default=600)
    run_stage.add_argument("--template-root")
    run_stage.add_argument("--state-memory-dir")
    run_stage.add_argument("--verify-threshold", type=float, default=0.86)
    run_stage.add_argument("--formation", action="store_true")
    run_stage.add_argument("--formation-index", type=int, default=0)
    run_stage.add_argument("--add-trust", action="store_true")
    run_stage.add_argument("--ignore-requirements", action="store_true")
    run_stage.add_argument("--operbox-data")
    run_stage.add_argument("--support-unit-usage", type=int, default=0)
    run_stage.add_argument("--is-raid", action="store_true")
    run_stage.add_argument("--confirm-formal-battle", action="store_true")
    run_stage.set_defaults(handler=_run_stage)

    run_campaign = subparsers.add_parser(
        "run-campaign",
        help="run several formal clears deterministically",
    )
    add_common(run_campaign)
    run_campaign.add_argument("--stages", nargs="+", required=True)
    run_campaign.add_argument("--jobs-root", required=True)
    run_campaign.add_argument("--roster", required=True)
    run_campaign.add_argument("--operbox-data")
    run_campaign.add_argument("--dll")
    run_campaign.add_argument("--resource-dir")
    run_campaign.add_argument("--user-dir")
    run_campaign.add_argument("--connect-config", default="General")
    run_campaign.add_argument("--timeout", type=int, default=600)
    run_campaign.add_argument("--template-root")
    run_campaign.add_argument("--state-memory-dir")
    run_campaign.add_argument(
        "--verify-threshold",
        type=float,
        default=DEFAULT_VERIFY_THRESHOLD,
    )
    run_campaign.add_argument("--campaign-id")
    run_campaign.add_argument("--resume-campaign", action="store_true")
    run_campaign.add_argument("--recovery-evidence")
    run_campaign.add_argument("--max-attempts-per-stage", type=int, default=3)
    run_campaign.add_argument("--max-total-attempts", type=int, default=6)
    run_campaign.add_argument("--max-elapsed-seconds", type=int, default=3600)
    run_campaign.add_argument("--max-sanity", type=int)
    run_campaign.add_argument(
        "--sanity-cost",
        action="append",
        default=[],
        metavar="STAGE=SANITY",
    )
    run_campaign.add_argument(
        "--round-name",
        default="localai-debug-campaign",
    )
    run_campaign.add_argument(
        "--confirm-formal-battle",
        action="store_true",
    )
    run_campaign.set_defaults(handler=_run_campaign)

    recognition_check = subparsers.add_parser(
        "recognition-check",
        help="evaluate an external real-screenshot acceptance corpus",
    )
    recognition_check.add_argument(
        "--package-dir",
        default=str(_default_package_dir()),
    )
    recognition_check.add_argument("--template-root")
    recognition_check.add_argument("--corpus", required=True)
    recognition_check.add_argument("--threshold", type=float, default=0.86)
    recognition_check.add_argument("--report")
    recognition_check.set_defaults(handler=_recognition_check)

    status = subparsers.add_parser("status", help="read one run status")
    status.add_argument("--evidence-root", default=str(_default_evidence_root()))
    status.add_argument("--run-id")
    status.set_defaults(handler=_status)

    events = subparsers.add_parser("events", help="read incremental run events")
    events.add_argument("--evidence-root", default=str(_default_evidence_root()))
    events.add_argument("--run-id")
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--limit", type=int, default=1000)
    events.set_defaults(handler=_events)

    campaign_status = subparsers.add_parser(
        "campaign-status",
        help="read one persisted campaign queue",
    )
    campaign_status.add_argument(
        "--evidence-root",
        default=str(_default_evidence_root()),
    )
    campaign_status.add_argument("--campaign-id", required=True)
    campaign_status.set_defaults(handler=_campaign_status)

    stop = subparsers.add_parser(
        "stop",
        help="request a cooperative stop and preserve pre-stop evidence",
    )
    add_common(stop)
    stop.add_argument("--run-id")
    stop.add_argument("--reason", default="operator-requested stop")
    stop.add_argument("--requested-by", default="codex")
    stop.set_defaults(handler=_stop)

    replay = subparsers.add_parser(
        "replay",
        help="re-run result recognition against saved evidence",
    )
    replay.add_argument("--package-dir", default=str(_default_package_dir()))
    replay.add_argument(
        "--evidence-root",
        default=str(_default_evidence_root()),
    )
    replay.add_argument("--run-id")
    replay.add_argument("--image")
    replay.add_argument("--template-root")
    replay.add_argument("--threshold", type=float, default=0.86)
    replay.set_defaults(handler=_replay)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        envelope, exit_code = arguments.handler(arguments)
    except (
        AdbError,
        BudgetExceeded,
        DeviceBusyError,
        EvidenceError,
        MaaCoreError,
        OSError,
        TimeoutError,
        ValueError,
    ) as exception:
        envelope = result_envelope(
            arguments.command,
            "FAILED",
            run_id=getattr(arguments, "run_id", None),
            data={},
            errors=[f"{type(exception).__name__}: {exception}"],
        )
        exit_code = 2
    print(json.dumps(envelope, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
