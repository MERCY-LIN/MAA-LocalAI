"""Command-line entry point for offline auto-push planning and ADB checks."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

import cv2

from .adb import AdbClient, AdbError
from .maa_core import (
    MaaCoreError,
    extract_oper_box_details,
    run_copilot_file,
    run_custom_tasks,
    run_oper_box,
    summarize_events,
)
from .map_planner import StageMap
from .metrics import BudgetLimits, RoundMetrics, TestResult
from .model_client import LocalModelClient
from .planner import build_round_plan, load_roster, load_substitutions
from .prts import PrtsClient, PrtsError
from .recognizer import decode_png
from .state_memory import VisualStateMemory
from .verifier import BattleResultVerifier, DEFAULT_TEMPLATE_ROOT, ResultVerification


def _default_template_root() -> str:
    repository_root = Path(__file__).resolve().parents[3]
    candidate = repository_root / "resource" / "template" / "Battle"
    return str(candidate if candidate.is_dir() else DEFAULT_TEMPLATE_ROOT)


def _budget_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--max-computer-use", type=int, default=None)
    parser.add_argument("--max-screenshots", type=int, default=40)
    parser.add_argument("--max-tool-calls", type=int, default=200)
    parser.add_argument("--max-input-tokens", type=int, default=12_000)
    parser.add_argument("--max-output-tokens", type=int, default=4_000)
    parser.add_argument("--max-strategy-runs", type=int, default=6)
    return parser


def _limits(arguments: argparse.Namespace) -> BudgetLimits:
    return BudgetLimits(
        computer_use_operations=arguments.max_computer_use,
        screenshots=arguments.max_screenshots,
        tool_calls=arguments.max_tool_calls,
        deepseek_input_tokens=arguments.max_input_tokens,
        deepseek_output_tokens=arguments.max_output_tokens,
        strategy_runs=arguments.max_strategy_runs,
    )


def _analyze(arguments: argparse.Namespace) -> int:
    metrics = RoundMetrics(
        round_name=arguments.round_name,
        stage=arguments.stage_id,
        limits=_limits(arguments),
    )
    metrics.add_note("Computer Use operations are counted but not invoked by this CLI.")
    stage_map = StageMap.load(arguments.map, metrics=metrics)
    roster = load_roster(arguments.roster, metrics=metrics)
    substitutions = load_substitutions(arguments.substitutions, metrics=metrics)
    model_client = None
    if arguments.model:
        model_client = LocalModelClient(
            endpoint=arguments.endpoint,
            model=arguments.model,
            timeout_seconds=arguments.model_timeout,
        )

    strategy_paths = [Path(value) for value in arguments.strategies]
    plan = build_round_plan(
        stage_id=arguments.stage_id,
        stage_map=stage_map,
        strategy_paths=strategy_paths,
        roster=roster,
        substitutions=substitutions,
        metrics=metrics,
        model_client=model_client,
        allow_support=not arguments.no_support,
        excluded_strategy_ids=arguments.exclude or (),
    )
    metrics.add_test(
        TestResult(
            name="dry-run-completed",
            success=True,
            detail="No emulator input was issued.",
        )
    )
    metrics.finish()
    output_path = Path(arguments.output) if arguments.output else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    report_path = metrics.write(arguments.report)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    print(f"\nMetrics report: {report_path}")
    return 0 if plan["selected_strategy"] is not None else 2


def _adb_check(arguments: argparse.Namespace) -> int:
    metrics = RoundMetrics(
        round_name=arguments.round_name,
        stage="adb-check",
        limits=_limits(arguments),
    )
    client = AdbClient(
        adb_path=arguments.adb,
        serial=arguments.serial,
        metrics=metrics,
    )
    exit_code = 0
    try:
        devices = client.devices()
        metrics.add_test(
            TestResult(
                name="adb-devices",
                success=arguments.serial in devices,
                detail=",".join(devices),
            )
        )
        if arguments.capture is not None:
            Path(arguments.capture).write_bytes(client.capture_png())
            metrics.add_test(
                TestResult(
                    name="adb-screenshot",
                    success=True,
                    detail=arguments.capture,
                )
            )
    except (AdbError, OSError) as exception:
        exit_code = 1
        metrics.add_test(
            TestResult(
                name="adb-runtime",
                success=False,
                detail=str(exception),
            )
        )
    finally:
        metrics.finish()
        report_path = metrics.write(arguments.report)
    print(f"Metrics report: {report_path}")
    return exit_code


def _adb_action(arguments: argparse.Namespace) -> int:
    metrics = RoundMetrics(
        round_name=arguments.round_name,
        stage="adb-action",
        limits=_limits(arguments),
    )
    client = AdbClient(
        adb_path=arguments.adb,
        serial=arguments.serial,
        metrics=metrics,
        minimum_capture_interval_seconds=0,
    )
    exit_code = 0
    try:
        if arguments.back:
            client.back()
        if arguments.tap:
            x, y = (int(value) for value in arguments.tap.split(","))
            client.click(x, y)
        if arguments.swipe:
            values = [int(value) for value in arguments.swipe.split(",")]
            if len(values) == 4:
                values.append(300)
            client.swipe(*values)
        if arguments.capture is not None:
            time.sleep(max(0, arguments.post_action_delay_ms) / 1000)
        if arguments.capture is not None:
            Path(arguments.capture).write_bytes(client.capture_png(force=True))
        metrics.add_test(
            TestResult(
                name="adb-action",
                success=True,
                detail=(
                    f"back={arguments.back} tap={arguments.tap} "
                    f"swipe={arguments.swipe}"
                ),
            )
        )
    except (AdbError, OSError, ValueError) as exception:
        exit_code = 1
        metrics.add_test(
            TestResult(
                name="adb-action",
                success=False,
                detail=str(exception),
            )
        )
    finally:
        metrics.finish()
        report_path = metrics.write(arguments.report)
    print(f"Metrics report: {report_path}")
    return exit_code


def _run_copilot(arguments: argparse.Namespace) -> int:
    metrics = RoundMetrics(
        round_name=arguments.round_name,
        stage=arguments.stage,
        limits=_limits(arguments),
    )
    exit_code = 2
    try:
        events = run_copilot_file(
            dll_path=arguments.dll,
            resource_dir=arguments.resource_dir,
            user_dir=arguments.user_dir,
            adb_path=arguments.adb,
            address=arguments.address,
            connect_config=arguments.connect_config,
            copilot_file=arguments.copilot,
            metrics=metrics,
            timeout_seconds=arguments.timeout,
            exercise=arguments.exercise,
        )
        summary = summarize_events(events)
        task_success = bool(
            summary["task_completed"] and not summary["task_errors"]
        )
        metrics.add_test(
            TestResult(
                name="maa-copilot-completed",
                success=task_success,
                detail=(
                    f"actions={summary['copilot_action_count']} "
                    f"errors={len(summary['task_errors'])}"
                ),
                strategy_path=str(Path(arguments.copilot).resolve()),
            )
        )
        verified_three_star = False
        if task_success and not arguments.no_verify:
            try:
                adb_client = AdbClient(
                    adb_path=arguments.adb,
                    serial=arguments.address,
                    metrics=metrics,
                    minimum_capture_interval_seconds=0,
                )
                image = decode_png(adb_client.capture_png(force=True))
                verification = BattleResultVerifier(
                    arguments.template_root,
                    threshold=arguments.verify_threshold,
                ).verify(image, metrics=metrics)
                verified_three_star = verification.status == "three_star"
                metrics.add_test(
                    TestResult(
                        name="result-verification",
                        success=verified_three_star,
                        detail=verification.status,
                        strategy_path=str(Path(arguments.copilot).resolve()),
                        stars=verification.stars,
                    )
                )
                summary["verification"] = asdict(verification)
                summary["verified"] = verified_three_star
            except (AdbError, OSError, ValueError) as exception:
                metrics.add_test(
                    TestResult(
                        name="result-verification",
                        success=False,
                        detail=str(exception),
                    )
                )
                summary["verification_error"] = str(exception)
        exit_code = 0 if task_success and (
            arguments.no_verify or verified_three_star
        ) else 2
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except (MaaCoreError, OSError, TimeoutError, ValueError) as exception:
        metrics.add_test(
            TestResult(
                name="maa-copilot-runtime",
                success=False,
                detail=str(exception),
            )
        )
    finally:
        metrics.finish()
        report_path = metrics.write(arguments.report)
    print(f"Metrics report: {report_path}")
    return exit_code


def _recognize_roster(arguments: argparse.Namespace) -> int:
    metrics = RoundMetrics(
        round_name=arguments.round_name,
        stage="oper-box",
        limits=_limits(arguments),
    )
    exit_code = 2
    try:
        events = run_oper_box(
            dll_path=arguments.dll,
            resource_dir=arguments.resource_dir,
            user_dir=arguments.user_dir,
            adb_path=arguments.adb,
            address=arguments.address,
            connect_config=arguments.connect_config,
            metrics=metrics,
            timeout_seconds=arguments.timeout,
        )
        details = extract_oper_box_details(events)
        if details is None:
            raise MaaCoreError("OperBox completed without own_opers")
        own_opers = details.get("own_opers") or []
        output_path = Path(arguments.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(details, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        metrics.add_test(
            TestResult(
                name="oper-box-recognition",
                success=bool(own_opers),
                detail=f"own_opers={len(own_opers)}",
                strategy_path=str(output_path.resolve()),
            )
        )
        exit_code = 0 if own_opers else 2
        print(
            json.dumps(
                {
                    "own_operators": len(own_opers),
                    "output": str(output_path.resolve()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except (MaaCoreError, OSError, TimeoutError, ValueError) as exception:
        metrics.add_test(
            TestResult(
                name="oper-box-runtime",
                success=False,
                detail=str(exception),
            )
        )
    finally:
        metrics.finish()
        report_path = metrics.write(arguments.report)
    print(f"Metrics report: {report_path}")
    return exit_code


def _run_custom(arguments: argparse.Namespace) -> int:
    metrics = RoundMetrics(
        round_name=arguments.round_name,
        stage="custom-task",
        limits=_limits(arguments),
    )
    exit_code = 2
    try:
        events = run_custom_tasks(
            dll_path=arguments.dll,
            resource_dir=arguments.resource_dir,
            user_dir=arguments.user_dir,
            adb_path=arguments.adb,
            address=arguments.address,
            connect_config=arguments.connect_config,
            task_names=arguments.task_names,
            metrics=metrics,
            timeout_seconds=arguments.timeout,
        )
        summary = summarize_events(events)
        exit_code = 0 if summary["task_completed"] else 2
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except (MaaCoreError, OSError, TimeoutError, ValueError) as exception:
        metrics.add_test(
            TestResult(
                name="custom-runtime",
                success=False,
                detail=str(exception),
            )
        )
    finally:
        metrics.finish()
        report_path = metrics.write(arguments.report)
    print(f"Metrics report: {report_path}")
    return exit_code


def _fetch_jobs(arguments: argparse.Namespace) -> int:
    metrics = RoundMetrics(
        round_name=arguments.round_name,
        stage=arguments.stage,
        limits=_limits(arguments),
    )
    exit_code = 2
    try:
        client = PrtsClient(metrics=metrics)
        jobs = client.query_jobs(arguments.stage, count=arguments.count)
        paths = client.save_jobs(jobs, arguments.output_dir)
        metrics.add_test(
            TestResult(
                name="prts-stage-query",
                success=bool(jobs),
                detail=f"stage={arguments.stage} jobs={len(jobs)}",
            )
        )
        exit_code = 0 if jobs else 2
        print(
            json.dumps(
                {
                    "stage": arguments.stage,
                    "jobs": len(jobs),
                    "files": [str(path.resolve()) for path in paths],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except (PrtsError, OSError, ValueError) as exception:
        metrics.add_test(
            TestResult(
                name="prts-stage-query",
                success=False,
                detail=str(exception),
            )
        )
    finally:
        metrics.finish()
        report_path = metrics.write(arguments.report)
    print(f"Metrics report: {report_path}")
    return exit_code


def _verify_result(arguments: argparse.Namespace) -> int:
    metrics = RoundMetrics(
        round_name=arguments.round_name,
        stage=arguments.stage,
        limits=_limits(arguments),
    )
    image = cv2.imread(str(Path(arguments.image)), cv2.IMREAD_COLOR)
    exit_code = 1
    if image is None:
        metrics.add_test(
            TestResult(
                name="result-image",
                success=False,
                detail="unable to load image",
            )
        )
        result: ResultVerification | None = None
    else:
        verifier = BattleResultVerifier(
            arguments.template_root,
            threshold=arguments.threshold,
        )
        result = verifier.verify(image, metrics=metrics)
        metrics.add_test(
            TestResult(
                name="result-verification",
                success=result.status == "three_star",
                detail=result.status,
                stars=result.stars,
            )
        )
        exit_code = 0 if result.status == "three_star" else 2
    metrics.finish()
    report_path = metrics.write(arguments.report)
    print(
        json.dumps(
            asdict(result) if result is not None else {"status": "unreadable"},
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"Metrics report: {report_path}")
    return exit_code


def _recognize_state(arguments: argparse.Namespace) -> int:
    metrics = RoundMetrics(
        round_name=arguments.round_name,
        stage="visual-state",
        limits=_limits(arguments),
    )
    image = cv2.imread(str(Path(arguments.image)), cv2.IMREAD_COLOR)
    if image is None:
        metrics.add_test(
            TestResult(
                name="state-image",
                success=False,
                detail="unable to load image",
            )
        )
        exit_code = 1
        match = None
    else:
        memory = VisualStateMemory.load(
            arguments.memory_dir,
            maximum_distance=arguments.maximum_distance,
            minimum_margin=arguments.minimum_margin,
            metrics=metrics,
        )
        match = memory.recognize(image, metrics=metrics)
        metrics.add_test(
            TestResult(
                name="state-recognition",
                success=match.known,
                detail=(
                    f"state={match.name} distance={match.distance} "
                    f"confidence={match.confidence}"
                ),
            )
        )
        exit_code = 0 if match.known else 2
    metrics.finish()
    report_path = metrics.write(arguments.report)
    print(
        json.dumps(
            {
                "state": match.name if match else "unreadable",
                "known": bool(match and match.known),
                "distance": match.distance if match else None,
                "confidence": match.confidence if match else None,
                "reference": (
                    str(match.reference_path)
                    if match and match.reference_path
                    else None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"Metrics report: {report_path}")
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maa-autopush",
        description="Local-first MAA auto-push planning core",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser(
        "analyze",
        parents=[_budget_parser()],
        help="analyze a map and rank local Copilot strategies",
    )
    analyze.add_argument("--stage-id", required=True)
    analyze.add_argument("--map", required=True)
    analyze.add_argument("--roster", required=True)
    analyze.add_argument("--strategies", nargs="+", required=True)
    analyze.add_argument("--substitutions")
    analyze.add_argument("--no-support", action="store_true")
    analyze.add_argument(
        "--exclude",
        nargs="+",
        help="Copilot job paths that must not be selected again",
    )
    analyze.add_argument("--model", help="optional local model ID")
    analyze.add_argument("--endpoint", default="http://127.0.0.1:11434/v1")
    analyze.add_argument("--model-timeout", type=int, default=90)
    analyze.add_argument("--output")
    analyze.add_argument("--report", required=True)
    analyze.add_argument("--round-name", default="auto-push-planning")
    analyze.set_defaults(handler=_analyze)

    adb_check = subparsers.add_parser(
        "adb-check",
        parents=[_budget_parser()],
        help="verify ADB connectivity and optionally capture one screenshot",
    )
    adb_check.add_argument("--adb", required=True)
    adb_check.add_argument("--serial", default="emulator-5554")
    adb_check.add_argument("--capture")
    adb_check.add_argument("--report", required=True)
    adb_check.add_argument("--round-name", default="auto-push-adb-check")
    adb_check.set_defaults(handler=_adb_check)

    adb_action = subparsers.add_parser(
        "adb-action",
        parents=[_budget_parser()],
        help="perform one bounded ADB action and optionally capture a screenshot",
    )
    adb_action.add_argument("--adb", required=True)
    adb_action.add_argument("--serial", default="emulator-5554")
    adb_action.add_argument("--back", action="store_true")
    adb_action.add_argument("--tap", help="x,y")
    adb_action.add_argument(
        "--swipe",
        help="x1,y1,x2,y2[,duration_ms]",
    )
    adb_action.add_argument("--capture")
    adb_action.add_argument("--post-action-delay-ms", type=int, default=800)
    adb_action.add_argument("--report", required=True)
    adb_action.add_argument("--round-name", default="auto-push-adb-action")
    adb_action.set_defaults(handler=_adb_action)

    run_copilot = subparsers.add_parser(
        "run-copilot",
        parents=[_budget_parser()],
        help="execute one local Copilot file through MaaCore.dll",
    )
    run_copilot.add_argument("--stage", required=True)
    run_copilot.add_argument("--dll", required=True)
    run_copilot.add_argument("--resource-dir", required=True)
    run_copilot.add_argument("--user-dir", required=True)
    run_copilot.add_argument("--adb", required=True)
    run_copilot.add_argument("--address", default="emulator-5554")
    run_copilot.add_argument("--connect-config", default="General")
    run_copilot.add_argument("--copilot", required=True)
    run_copilot.add_argument("--timeout", type=int, default=600)
    run_copilot.add_argument("--exercise", action="store_true")
    run_copilot.add_argument("--no-verify", action="store_true")
    run_copilot.add_argument("--template-root", default=_default_template_root())
    run_copilot.add_argument("--verify-threshold", type=float, default=0.86)
    run_copilot.add_argument("--report", required=True)
    run_copilot.add_argument("--round-name", default="auto-push-copilot")
    run_copilot.set_defaults(handler=_run_copilot)

    recognize_roster = subparsers.add_parser(
        "recognize-roster",
        parents=[_budget_parser()],
        help="recognize the account roster through MAA OperBox",
    )
    recognize_roster.add_argument("--dll", required=True)
    recognize_roster.add_argument("--resource-dir", required=True)
    recognize_roster.add_argument("--user-dir", required=True)
    recognize_roster.add_argument("--adb", required=True)
    recognize_roster.add_argument("--address", default="emulator-5554")
    recognize_roster.add_argument("--connect-config", default="General")
    recognize_roster.add_argument("--timeout", type=int, default=900)
    recognize_roster.add_argument("--output", required=True)
    recognize_roster.add_argument("--report", required=True)
    recognize_roster.add_argument("--round-name", default="auto-push-roster")
    recognize_roster.set_defaults(handler=_recognize_roster)

    run_custom = subparsers.add_parser(
        "run-custom",
        parents=[_budget_parser()],
        help="execute one or more MAA Custom task names",
    )
    run_custom.add_argument("--dll", required=True)
    run_custom.add_argument("--resource-dir", required=True)
    run_custom.add_argument("--user-dir", required=True)
    run_custom.add_argument("--adb", required=True)
    run_custom.add_argument("--address", default="emulator-5554")
    run_custom.add_argument("--connect-config", default="General")
    run_custom.add_argument("--task-names", nargs="+", required=True)
    run_custom.add_argument("--timeout", type=int, default=600)
    run_custom.add_argument("--report", required=True)
    run_custom.add_argument("--round-name", default="auto-push-custom")
    run_custom.set_defaults(handler=_run_custom)

    fetch_jobs = subparsers.add_parser(
        "fetch-jobs",
        parents=[_budget_parser()],
        help="download public Copilot jobs for one exact PRTS stage",
    )
    fetch_jobs.add_argument("--stage", required=True)
    fetch_jobs.add_argument("--count", type=int, default=20)
    fetch_jobs.add_argument("--output-dir", required=True)
    fetch_jobs.add_argument("--report", required=True)
    fetch_jobs.add_argument("--round-name", default="auto-push-fetch-jobs")
    fetch_jobs.set_defaults(handler=_fetch_jobs)

    verify_result = subparsers.add_parser(
        "verify-result",
        parents=[_budget_parser()],
        help="verify a captured settlement screenshot with MAA templates",
    )
    verify_result.add_argument("--stage", required=True)
    verify_result.add_argument("--image", required=True)
    verify_result.add_argument(
        "--template-root",
        default=_default_template_root(),
    )
    verify_result.add_argument("--threshold", type=float, default=0.86)
    verify_result.add_argument("--report", required=True)
    verify_result.add_argument("--round-name", default="auto-push-result-verification")
    verify_result.set_defaults(handler=_verify_result)

    recognize_state = subparsers.add_parser(
        "recognize-state",
        parents=[_budget_parser()],
        help="recognize a screenshot using local visual state memory",
    )
    recognize_state.add_argument("--image", required=True)
    recognize_state.add_argument("--memory-dir", required=True)
    recognize_state.add_argument("--maximum-distance", type=int, default=8)
    recognize_state.add_argument("--minimum-margin", type=int, default=2)
    recognize_state.add_argument("--report", required=True)
    recognize_state.add_argument("--round-name", default="auto-push-state-recognition")
    recognize_state.set_defaults(handler=_recognize_state)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    return int(arguments.handler(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
