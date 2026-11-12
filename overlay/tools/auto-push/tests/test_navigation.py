from pathlib import Path

from autopush.metrics import BudgetLimits, RoundMetrics
from autopush.navigation import AdbStateNavigator, NavigationError
from autopush.state_memory import StateMatch


class FakeMemory:
    def __init__(self, matches):
        self.matches = iter(matches)

    def recognize(self, _image, *, metrics=None):
        return next(self.matches)


class FakeClient:
    def __init__(self):
        self.clicks = []

    def capture_png(self):
        return b"image"

    def capture_frame(self):
        return type(
            "Frame",
            (),
            {
                "payload": b"image",
                "metadata": lambda self: {
                    "sequence": 1,
                    "action_sequence": 0,
                    "captured_at": "test",
                    "cache_hit": False,
                },
            },
        )()

    def click(self, x, y):
        self.clicks.append((x, y))


def match(name: str, known: bool = True) -> StateMatch:
    return StateMatch(
        name=name,
        distance=0 if known else 64,
        confidence=0.9 if known else 0,
        known=known,
        reference_path=Path(f"{name}.png") if known else None,
    )


def test_navigation_skips_unknown_loading_state(monkeypatch):
    monkeypatch.setattr(
        "autopush.navigation.decode_png",
        lambda _payload: object(),
    )
    client = FakeClient()
    metrics = RoundMetrics(
        round_name="navigation",
        stage="test",
        limits=BudgetLimits(screenshots=3, tool_calls=10),
    )
    navigator = AdbStateNavigator(
        client,
        FakeMemory([match("unknown", False), match("home")]),
        metrics,
        poll_interval_seconds=0,
    )
    state = navigator.wait_for_known(
        timeout_seconds=1,
        allowed_states={"home"},
    )
    assert state.name == "home"
    assert not client.clicks


def test_safe_tap_requires_allowed_state(monkeypatch):
    monkeypatch.setattr(
        "autopush.navigation.decode_png",
        lambda _payload: object(),
    )
    client = FakeClient()
    metrics = RoundMetrics(round_name="navigation", stage="test")
    navigator = AdbStateNavigator(
        client,
        FakeMemory([match("home"), match("terminal")]),
        metrics,
        poll_interval_seconds=0,
    )
    state = navigator.safe_tap(
        100,
        200,
        allowed_states={"home"},
        post_action_states={"terminal"},
        timeout_seconds=1,
    )
    assert state.name == "terminal"
    assert client.clicks == [(100, 200)]
