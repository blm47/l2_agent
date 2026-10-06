import threading
from itertools import pairwise
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from hid_pilot.loops import KeyLoop, run_key_loops


def test_rows_keep_separate_intervals_and_counts():
    now = [0.0]
    calls = []
    controller = Mock()
    controller.wait.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)

    def key(name, duration):
        calls.append((name, now[0]))
        now[0] += duration / 1000

    controller.key.side_effect = key
    plans = (KeyLoop(row=0, key="R", interval_ms=200, repeats=3),
             KeyLoop(row=1, key="1", interval_ms=500, repeats=2))
    run_key_loops(controller, plans, {i: threading.Event() for i in (0, 1)},
                  lambda _: None, lambda: now[0])
    assert [key for key, _ in calls].count("R") == 3
    assert [key for key, _ in calls].count("1") == 2
    for name, interval in (("R", .3), ("1", .6)):
        times = [t for key, t in calls if key == name]
        assert all(b - a >= interval - 1e-6 for a, b in pairwise(times))


def test_infinite_row_can_be_cancelled_without_affecting_other_row():
    controller = Mock()
    cancelled = {i: threading.Event() for i in (0, 1)}
    controller.key.side_effect = lambda name, ms: cancelled[0].set() if name == "R" else None
    run_key_loops(controller, (
        KeyLoop(row=0, key="R", interval_ms=150, repeats=-1),
        KeyLoop(row=1, key="1", interval_ms=150, repeats=1),
    ), cancelled, lambda _: None)
    assert controller.key.call_count == 2


@pytest.mark.parametrize("changes", [{"repeats": 0}, {"interval_ms": 0}, {"key": "BACKSLASH"}])
def test_invalid_row_is_rejected(changes):
    with pytest.raises(ValidationError):
        KeyLoop(**({"row": 0, "key": "R", "interval_ms": 500, "repeats": 1} | changes))


def test_mouse_loop_uses_current_point_controller():
    controller = Mock()
    run_key_loops(controller, (KeyLoop(row=0, key="LMB", interval_ms=150, repeats=1),),
                  {0: threading.Event()}, lambda _: None)
    controller.click_current.assert_called_once_with("left")
    controller.key.assert_not_called()
