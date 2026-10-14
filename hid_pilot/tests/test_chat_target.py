from unittest.mock import Mock

import pytest

from hid_pilot.controller import ActionController
from hid_pilot.transport import PicoTransport


def controller():
    value = ActionController.__new__(ActionController)
    value.transport = Mock(protocol="HID_PILOT_V4")
    value.desktop = Mock()
    value.desktop.target.rect = (0, 0, 200, 200)
    value.desktop.cursor.return_value = (100, 100)
    value.key = Mock()
    value._hold = Mock()
    value.wait = Mock()
    value.click_current = Mock()
    value.click = Mock()
    return value


def test_chat_sequence_clears_types_confirms_and_right_clicks():
    value = controller()
    trace = Mock()
    for method in ("key", "_hold", "click_current"):
        trace.attach_mock(getattr(value, method), method)
    value.target_by_name("Cloudy Beast")
    calls = [(call[0], call.args) for call in trace.mock_calls]
    assert calls[:3] == [("key", ("ENTER", 80)), ("_hold", ("CHORD", "CTRL+A", 80)),
                         ("key", ("BACKSPACE", 80))]
    assert calls[3:11] == [("key", (key, 50)) for key in ("/", "T", "A", "R", "G", "E", "T", "SPACE")]
    assert ("_hold", ("CHORD", "SHIFT+C", 50)) in calls
    assert calls[-2:] == [("key", ("ENTER", 80)), ("click_current", ("right",))]


def test_chat_moves_inside_window_when_cursor_outside():
    value = controller()
    value.desktop.cursor.return_value = (-1, -1)
    value.target_by_name("Gremlin")
    value.click.assert_called_once_with("right", .5, .5)
    value.click_current.assert_not_called()


@pytest.mark.parametrize("name", ["", "bad\n/quit", "x" * 81, "bad;name"])
def test_chat_rejects_invalid_names_before_input(name):
    value = controller()
    with pytest.raises(ValueError):
        value.target_by_name(name)
    value.key.assert_not_called()


def test_chat_rejects_old_firmware_before_input():
    value = controller()
    value.transport.protocol = "HID_PILOT_V3"
    with pytest.raises(ValueError, match="HID_PILOT_V4"):
        value.target_by_name("Gremlin")
    value.key.assert_not_called()


@pytest.mark.parametrize("chord", ["ALT+TAB", "CTRL+ESC", "WIN+R", "SHIFT+ENTER"])
def test_transport_rejects_unapproved_chords(chord):
    transport = PicoTransport.__new__(PicoTransport)
    transport.protocol = "HID_PILOT_V4"
    transport.command = Mock()
    with pytest.raises(ValueError):
        transport.hold("CHORD", chord, 80)
    transport.command.assert_not_called()
