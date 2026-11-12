from autopush.adb import AdbClient
from autopush.metrics import BudgetLimits, RoundMetrics


def test_adb_capture_is_throttled(monkeypatch):
    metrics = RoundMetrics(
        round_name="capture",
        stage="test",
        limits=BudgetLimits(screenshots=1),
    )
    client = AdbClient(
        "adb.exe",
        metrics=metrics,
        minimum_capture_interval_seconds=60,
    )
    calls = 0

    def fake_run(*arguments):
        nonlocal calls
        calls += 1
        return b"image"

    monkeypatch.setattr(client, "_run", fake_run)
    assert client.capture_png() == b"image"
    assert client.capture_png() == b"image"
    assert calls == 1
    assert metrics.counters["screenshots"] == 1
