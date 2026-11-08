import pytest

from hid_pilot.controller import Stopped
from test_chat_target import controller as chat_controller
from test_melee_farm import harness


@pytest.mark.parametrize("name", ["Mob™", "bad\n/quit", "bad;name", "x" * 81, ""])
def test_bad_ocr_name_is_skipped_without_chat_input_or_farm_stop(name, caplog):
    farmer, controller, _, state = harness()
    farmer.known_mobs = {name: 1, "Gremlin": 1}
    farmer.target_name_pool = ["Gremlin", name]
    chat = chat_controller()

    def select(value):
        chat.target_by_name(value)
        state[2] = .9

    controller.target_by_name.side_effect = select
    assert farmer.search_known_target() == .9
    assert [call.args[0] for call in controller.target_by_name.call_args_list] == [name, "Gremlin"]
    assert name not in farmer.known_mobs
    assert "продолжаем поиск моба" in caplog.text
    assert chat.key.call_args_list[0].args == ("ENTER", 80)


def test_only_bad_ocr_name_returns_to_normal_search_without_input():
    farmer, controller, _, _ = harness()
    farmer.known_mobs = {"Mob™": 1}
    chat = chat_controller()
    controller.target_by_name.side_effect = chat.target_by_name
    assert farmer.search_known_target() is None
    assert farmer.search_known_target() is None
    assert controller.target_by_name.call_count == 1
    chat.key.assert_not_called()


@pytest.mark.parametrize("error", [Stopped("STOP"), ValueError("HID_PILOT_V4"), RuntimeError("HID")])
def test_skipping_bad_names_does_not_hide_controller_failures(error):
    farmer, controller, _, _ = harness()
    farmer.known_mobs = {"Gremlin": 1}
    controller.target_by_name.side_effect = error
    with pytest.raises(type(error), match=str(error)):
        farmer.search_known_target()
