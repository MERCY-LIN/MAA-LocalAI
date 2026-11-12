from autopush.adb import AdbClient
from autopush.metrics import RoundMetrics


def test_action_invalidates_cached_screenshot():
    metrics = RoundMetrics(round_name="adb-cache", stage="test")
    client = AdbClient(
        adb_path="adb.exe",
        serial="emulator-test-cache",
        metrics=metrics,
        minimum_capture_interval_seconds=60,
        device_control_timeout_seconds=0,
    )
    calls: list[tuple[str, ...]] = []
    screenshot_counter = 0

    def fake_run(*arguments: str) -> bytes:
        nonlocal screenshot_counter
        calls.append(arguments)
        if arguments[:2] == ("exec-out", "screencap"):
            screenshot_counter += 1
            return f"screen-{screenshot_counter}".encode()
        return b""

    client._run = fake_run  # type: ignore[method-assign]

    first = client.capture_frame()
    cached = client.capture_frame()
    client.click(100, 200)
    after_action = client.capture_frame()

    assert first.sequence == 1
    assert cached.sequence == 1
    assert cached.cache_hit
    assert after_action.sequence == 2
    assert after_action.action_sequence == 1
    assert not after_action.cache_hit
    assert after_action.payload == b"screen-2"
    assert (
        "shell",
        "input",
        "tap",
        "100",
        "200",
    ) in calls
