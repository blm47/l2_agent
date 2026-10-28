from types import SimpleNamespace
import threading
from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.support_life import SupportHPReader, SupportLifeObserver, village_button_template, village_button_visible
from hid_pilot.bars import BarBox
from test_duo import FARMER, SUPPORT, make_duo


def observer(values):
    controller = Mock(pause_generation=0)
    controller.capture.return_value = np.zeros((220, 700, 3), np.uint8)
    reader = Mock(side_effect=values)
    death, revived = Mock(), Mock()
    life = SupportLifeObserver(death, revived, reader, lambda: 0)
    return life, controller, reader, death, revived


def test_two_zero_hp_frames_notify_once_until_positive_hp():
    life, controller, reader, death, revived = observer([0, 0, 0, 0, 0])
    life.observe(controller, None)
    assert controller.capture.call_count == 2
    controller.wait.assert_called_once_with(.15)
    death.assert_called_once_with()
    life.observe(controller, None)
    assert death.call_count == 1
    life.observe(controller, .3)
    revived.assert_called_once_with()
    life.observe(controller, None)
    assert death.call_count == 2


@pytest.mark.parametrize("values", [[None], [.2], [0, None], [0, .5]])
def test_missing_or_unconfirmed_hp_never_means_death(values):
    life, controller, reader, death, revived = observer(values)
    life.observe(controller, None)
    death.assert_not_called()
    assert not life.dead
    controller.key.assert_not_called()
    controller.switch_window.assert_not_called()


def test_positive_party_hp_avoids_capture_and_ocr():
    life, controller, reader, death, revived = observer([])
    life.observe(controller, .4)
    controller.capture.assert_not_called()
    reader.assert_not_called()


def test_unknown_hp_does_not_rearm_dead_support():
    life, controller, reader, death, revived = observer([0, 0, None, 0])
    life.observe(controller, None)
    life.observe(controller, None)
    life.observe(controller, None)
    death.assert_called_once_with()
    revived.assert_not_called()


def test_stale_ocr_does_not_notify_death():
    life, controller, reader, death, revived = observer([0])
    life.clock = Mock(side_effect=[0, 1.1])
    life.observe(controller, None)
    death.assert_not_called()


def test_pause_between_confirmations_invalidates_death():
    life, controller, reader, death, revived = observer([0, 0])
    controller.wait.side_effect = lambda seconds: setattr(controller, "pause_generation", 1)
    life.observe(controller, None)
    death.assert_not_called()


def test_to_village_button_confirms_death_even_when_hp_is_unknown():
    life, controller, reader, death, revived = observer([None, None])
    life.button_reader = Mock(return_value=True)
    life.observe(controller, None)
    death.assert_called_once_with()
    assert controller.capture.call_count == 2
    controller.key.assert_not_called()
    controller.click.assert_not_called()


@pytest.mark.parametrize("values,buttons", [([None, None], [True, False]),
                                           ([.4], [True]), ([0, None], [False, True])])
def test_unconfirmed_or_conflicting_button_does_not_mean_death(values, buttons):
    life, controller, reader, death, revived = observer(values)
    life.button_reader = Mock(side_effect=buttons)
    life.observe(controller, None)
    death.assert_not_called()


def test_village_button_requires_the_reference_in_the_center_of_the_game():
    button = village_button_template()
    assert button is not None
    height, width = button.shape
    image = np.zeros((1325, 2120, 3), np.uint8)
    assert not village_button_visible(image)
    image[440:440 + height, 1000:1000 + width] = button[:, :, None]
    assert village_button_visible(image)
    image[:] = 0
    image[20:20 + height, 20:20 + width] = button[:, :, None]
    assert not village_button_visible(image)


def test_role_switch_validates_window_before_reading_support_hp():
    duo, controller = make_duo()
    life = Mock()
    life.observe.side_effect = lambda current, hp: (
        pytest.fail("Читается чужое окно") if controller.role != SUPPORT else None)
    duo.support_life = life
    duo.visit("F3", .1)
    life.observe.assert_called_once_with(controller, None)
    assert controller.events == [("window", SUPPORT.hwnd), (SUPPORT.hwnd, "F3"), ("window", FARMER.hwnd)]


def test_stale_party_hp_does_not_skip_own_hp_check():
    duo, controller = make_duo()
    duo.support_life = Mock()
    duo.previous_resources = {"support_hp": .8}
    duo.resources_at = controller.now - 2
    duo.visit("F3", .1)
    duo.support_life.observe.assert_called_once_with(controller, None)


@pytest.mark.parametrize("candidates,expected", [
    ([("cp", 0, .99), ("hp", 0, .99), ("mp", 1, .99)], 0),
    ([("cp", 0, .99), ("mp", 1, .99)], None),
    ([("target_hp", 0, .99)], None),
    ([("hp", 0, .86)], None),
    ([("hp", 0, .99), ("hp", .5, .99)], None),
    ([], None),
])
def test_hp_requires_unique_labelled_numeric_pair(monkeypatch, candidates, expected):
    result = [SimpleNamespace(kind=kind, numbers=SimpleNamespace(fraction=value, confidence=score),
                              box=BarBox(x1=.4, y1=.3, x2=.6, y2=.4))
              for kind, value, score in candidates]
    detect = Mock(return_value=result)
    background = Mock()
    numbers = Mock()
    numbers.read.return_value = SimpleNamespace(fraction=expected, confidence=.99)
    monkeypatch.setattr("hid_pilot.support_life.background_numbers", lambda: background)
    monkeypatch.setattr("hid_pilot.support_life.shared_numbers", lambda: numbers)
    monkeypatch.setattr("hid_pilot.support_life.find_numbers", detect)
    image = np.zeros((1325, 2120, 3), np.uint8)
    reader = SupportHPReader()
    reader.prepare(image)
    reader.worker.join(3)
    assert not reader.worker.is_alive()
    if expected is None:
        assert reader.box is None
        numbers.read.assert_not_called()
    else:
        assert reader(image) == expected
    assert detect.call_args.args[0].shape == (220, 700, 3)
    assert detect.call_args.kwargs == {"top_only": False}


def test_located_hp_roi_reads_fresh_numbers_without_repeating_label_scan(monkeypatch):
    candidate = SimpleNamespace(kind="hp", numbers=SimpleNamespace(fraction=0, confidence=.99),
                                box=BarBox(x1=.4, y1=.3, x2=.6, y2=.4))
    detect = Mock(return_value=[candidate])
    numbers = Mock()
    numbers.read.return_value = SimpleNamespace(fraction=.2, confidence=.99)
    monkeypatch.setattr("hid_pilot.support_life.find_numbers", detect)
    monkeypatch.setattr("hid_pilot.support_life.shared_numbers", lambda: numbers)
    monkeypatch.setattr("hid_pilot.support_life.background_numbers", lambda: Mock())
    image = np.zeros((1325, 2120, 3), np.uint8)
    reader = SupportHPReader()
    assert reader(image) is None
    reader.worker.join(3)
    assert reader(image) == .2
    detect.assert_called_once()
    numbers.read.assert_called_once()
    assert numbers.read.call_args.args[0].shape == (22, 140, 3)


def test_slow_roi_search_is_bounded_and_does_not_return_old_hp(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    candidate = SimpleNamespace(kind="hp", numbers=SimpleNamespace(fraction=0, confidence=.99),
                                box=BarBox(x1=.4, y1=.3, x2=.6, y2=.4))

    def scan(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return [candidate]

    scan = Mock(side_effect=scan)
    numbers = Mock()
    numbers.read.return_value = SimpleNamespace(fraction=.7, confidence=.99)
    monkeypatch.setattr("hid_pilot.support_life.find_numbers", scan)
    monkeypatch.setattr("hid_pilot.support_life.background_numbers", lambda: Mock())
    monkeypatch.setattr("hid_pilot.support_life.shared_numbers", lambda: numbers)
    image = np.zeros((1325, 2120, 3), np.uint8)
    reader = SupportHPReader()
    try:
        assert reader(image) is None
        assert entered.wait(1)
        worker = reader.worker
        for _ in range(10):
            assert reader(image) is None
            assert reader.worker is worker
        numbers.read.assert_not_called()
        scan.assert_called_once()
    finally:
        release.set()
        worker.join(3)
    # Ноль из старого фонового кадра не публикуется: читается новый HP=.7.
    assert reader(image) == .7
