from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.bars import BarBox, BarProfile, bar_fraction
from hid_pilot.farm import FarmConfig, Farmer, ReacquireTarget
from hid_pilot.picker import color_command
from hid_pilot.script import parse_script


def harness(**changes):
    now = [0.0]
    state = {"hp": 1., "mp": 1., "target": 1.}
    controller = Mock()
    controller.wait.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
    farm = Farmer(controller, Mock(), FarmConfig(rest="I", cycles=1, **changes),
                  lambda _: None, lambda: now[0])
    farm.observe = lambda: (state["hp"], state["mp"], state["target"])
    return farm, controller, state


def test_attack_then_melee_and_optional_loot():
    for pickup in (True, False):
        farm, controller, state = harness(pickup_loot=pickup, loot_attempts=2)

        def press(key, ms, state=state):
            if key == "R":
                state["target"] = .08
            elif key == "4":
                state["target"] = 0.

        controller.key.side_effect = press
        farm.run()
        keys = [call.args[0] for call in controller.key.call_args_list]
        assert keys == ["3", "R", "4"] + (["5", "5"] if pickup else [])


def test_heal_forces_reacquisition_before_more_attack():
    farm, controller, state = harness()
    state["hp"] = .4

    def heal(key, ms):
        assert key == "2"
        state.update(hp=.8, target=None)

    controller.key.side_effect = heal
    with pytest.raises(ReacquireTarget):
        farm.care_target()
    assert controller.key.call_count == 1


def test_rest_sits_once_and_stands_at_ninety_percent():
    farm, controller, state = harness()
    state.update(mp=.05, target=0.)
    original_wait = controller.wait.side_effect

    def wait(seconds):
        original_wait(seconds)
        if farm.sitting:
            state["mp"] = min(1., state["mp"] + .15)

    controller.wait.side_effect = wait
    assert farm.care()
    assert state["mp"] >= .9
    assert [call.args[0] for call in controller.key.call_args_list] == ["I", "I"]
    assert not farm.sitting


def test_rest_accepts_recovery_on_deadline_observation():
    farm, controller, state = harness(rest_timeout=1)
    state.update(mp=.05, target=0.)
    wait = controller.wait.side_effect

    def recover(seconds):
        wait(seconds)
        if farm.clock() >= 1:
            state["mp"] = 1.

    controller.wait.side_effect = recover
    assert farm.care()
    assert not farm.sitting


def test_rest_timeout_reports_last_mp_and_source(caplog):
    farm, controller, state = harness(rest_timeout=5)
    state.update(mp=.05, target=0.)
    farm.observation_details["mp"] = "OCR 5/100"
    with (caplog.at_level("INFO", logger="hid_pilot.farm"),
          pytest.raises(RuntimeError, match="MP 5.0%.*OCR 5/100.*90.0%")):
        farm.care()
    progress = [record for record in caplog.records if record.message.startswith("Восстановление:")]
    assert len(progress) == 3
    assert controller.key.call_count == 1


def test_background_ocr_is_waited_before_fresh_capture(monkeypatch):
    from contextlib import contextmanager

    from hid_pilot.numbers import parse_numbers

    now = [0.]

    @contextmanager
    def delayed_lock():
        now[0] += 2
        yield

    reader = Mock()
    reader.lock = delayed_lock()
    reader.read.return_value = parse_numbers("148/148", .99)
    monkeypatch.setattr("hid_pilot.numbers.shared_numbers", lambda: reader)
    profile = BarProfile(width=200, height=100, numbers={
        name: BarBox(x1=0, x2=1, y1=0, y2=.2) for name in ("hp", "mp", "target_hp")
    })
    controller = Mock()
    controller.capture.side_effect = lambda: np.zeros((100, 200, 3), dtype=np.uint8)
    farm = Farmer(controller, profile, FarmConfig(rest="I"), lambda _: None, lambda: now[0])
    assert farm.observe() == (1., 1., 1.)
    assert farm.observation_details["mp"] == "OCR 148/148"


def test_heal_failure_is_bounded():
    farm, controller, state = harness(heal_attempts=2)
    state["hp"] = .4
    with pytest.raises(RuntimeError, match="Лечение"):
        farm.care()
    assert controller.key.call_count == 2


@pytest.mark.parametrize("movement", [True, False])
def test_unknown_player_bar_stops_after_bounded_retries(movement):
    controller = Mock()
    controller.active_time.return_value = 0.
    profile = Mock()
    profile.read.side_effect = [None, .9, .5] * 5
    farm = Farmer(controller, profile, FarmConfig(rest="I", observation_movement=movement),
                  lambda _: None)
    with pytest.raises(RuntimeError, match="не распознаны"):
        farm.run()
    assert controller.capture.call_count == 5
    assert [call.args[0] for call in controller.key.call_args_list] == (
        ["W", "A", "S"] if movement else []
    )


def test_missing_target_during_combat_does_not_mean_dead():
    farm, controller, state = harness()
    controller.key.side_effect = lambda key, ms: state.update(target=None) if key == "R" else None
    with pytest.raises(RuntimeError, match="смерть не подтверждена"):
        farm.run()
    assert "5" not in [call.args[0] for call in controller.key.call_args_list]


@pytest.mark.parametrize("name,color", [("hp", (0, 0, 180)), ("mp", (180, 0, 0))])
def test_bar_fraction_partial_and_unknown(name, color):
    image = np.zeros((6, 200, 3), dtype=np.uint8)
    image[:, :18] = color
    assert bar_fraction(image, name) == pytest.approx(.09)
    image[:] = 0
    assert bar_fraction(image, name) is None


def test_profile_rejects_resized_image():
    profile = BarProfile(width=200, height=100)
    with pytest.raises(RuntimeError, match="Размер"):
        profile.read(np.zeros((100, 201, 3), dtype=np.uint8), "hp")


def test_picker_rgb_and_coordinate_mapping():
    image = np.zeros((20, 40, 3), dtype=np.uint8)
    image[19, 39] = (17, 34, 51)
    command = color_command(image, 39, 19)
    assert command == "color 1.000000 1.000000 332211 15"
    assert parse_script(command)[0].operation == "color"


def test_variables_and_invalid_rest_key():
    assert parse_script("set attack R\nkey $attack 100")[0].args == ("R", "100")
    assert parse_script('set rest I\nset pickup_loot false\nfarm "farm-bars.json"')
    with pytest.raises(ValueError):
        parse_script('set rest 12\nfarm "farm-bars.json"')
    with pytest.raises(ValueError):
        parse_script("wait 1\nset attack R\nkey $attack 100")


def test_legacy_board_blocks_farm_before_first_key():
    controller = Mock()
    controller.transport.protocol = "L2_PICO_V1"
    with pytest.raises(RuntimeError, match="прошивки"):
        Farmer(controller, Mock(), FarmConfig(rest="="), lambda _: None)
    controller.key.assert_not_called()


def test_shipped_farm_script_parses_with_equals_and_default_loot():
    from hid_pilot.script import script_variables
    text = (Path(__file__).parents[1] / "extensions" / "scripts" / "farm.pilot").read_text("utf-8")
    assert parse_script(text)[0].operation == "farm"
    config = FarmConfig.model_validate(script_variables(text))
    assert config.rest == "="
    assert config.pickup_loot is True
    assert config.mp_resume == .9


def test_zero_requires_calibrated_visible_text_and_empty_red_bar():
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    image[20:30, 20:24] = 200
    image[20:30, 30:34] = 200
    profile = BarProfile(width=200, height=100,
                         bars={"target_hp": BarBox(x1=0, y1=.5, x2=1, y2=.6)})
    assert profile.read(image, "target_hp") is None
    profile.calibrate_zero(image, BarBox(x1=.05, y1=.15, x2=.3, y2=.35))
    assert profile.read(image, "target_hp") == 0
    absent = np.zeros_like(image)
    assert profile.read(absent, "target_hp") is None
    changed = image.copy()
    changed[20:30, 20:24] = 0
    assert profile.read(changed, "target_hp") is None
    image[50:60, :100] = (0, 0, 180)
    assert profile.read(image, "target_hp") == .5


def test_confirmed_zero_after_attack_proceeds_to_loot_without_melee():
    farm, controller, state = harness(loot_attempts=2)
    controller.key.side_effect = lambda key, ms: state.update(target=0.) if key == "R" else None
    farm.run()
    assert [call.args[0] for call in controller.key.call_args_list] == ["3", "R", "5", "5"]


def test_already_dead_target_is_not_farmed_again():
    farm, controller, state = harness(target_attempts=2)
    state["target"] = 0.
    with pytest.raises(RuntimeError, match="HP цели не найдено"):
        farm.run()
    assert [call.args[0] for call in controller.key.call_args_list] == ["3", "3"]


def test_stuck_target_triggers_melee_then_resumes_attacks():
    farm, controller, state = harness(stuck_timeout=2, reposition_delay=.5, pickup_loot=False)
    moved = [False]

    def press(key, ms):
        if key == "4":
            moved[0] = True
        elif key == "R" and moved[0]:
            state["target"] = 0.

    controller.key.side_effect = press
    farm.run()
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys == ["3", "R", "R", "4", "R"]


def test_unreachable_target_has_bounded_reposition_attempts():
    farm, controller, _state = harness(stuck_timeout=1, reposition_delay=.5,
                                      reposition_attempts=2)
    with pytest.raises(RuntimeError, match="после попыток сближения"):
        farm.run()
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys.count("4") == 2
    assert "5" not in keys


def test_regular_damage_does_not_trigger_obstacle_recovery():
    farm, controller, state = harness(stuck_timeout=2, pickup_loot=False)
    controller.key.side_effect = lambda key, ms: state.update(
        target=max(0., state["target"] - .25)
    ) if key == "R" else None
    farm.run()
    assert [call.args[0] for call in controller.key.call_args_list].count("4") == 0


def test_hp_increase_does_not_reset_no_damage_timer():
    farm, controller, state = harness(stuck_timeout=2, reposition_attempts=1,
                                      reposition_delay=.5)
    state["target"] = .5
    controller.key.side_effect = lambda key, ms: state.update(
        target=min(.9, state["target"] + .02)
    ) if key == "R" else None
    with pytest.raises(RuntimeError, match="после попыток сближения"):
        farm.run()
    assert [call.args[0] for call in controller.key.call_args_list].count("4") == 1


def test_missing_target_after_reposition_stops_without_loot():
    farm, controller, state = harness(stuck_timeout=1, reposition_delay=.5)
    controller.key.side_effect = lambda key, ms: state.update(target=None) if key == "4" else None
    with pytest.raises(RuntimeError, match="после сближения"):
        farm.run()
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys[-1] == "4"
    assert "5" not in keys


def test_low_hp_during_reposition_heals_and_reacquires():
    farm, controller, state = harness(stuck_timeout=1, reposition_delay=.5)

    def press(key, ms):
        if key == "4":
            state["hp"] = .4
        elif key == "2":
            state.update(hp=1., target=None)

    controller.key.side_effect = press
    with pytest.raises(ReacquireTarget):
        farm.fight()
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys[-2:] == ["4", "2"]
    assert "5" not in keys


@pytest.mark.parametrize("missing", ["hp", "mp"])
def test_unknown_bars_retry_and_recover_with_movement(missing):
    controller = Mock()
    controller.active_time.return_value = 0.
    profile = Mock()
    failed = [None, .9, .5] if missing == "hp" else [.9, None, .5]
    profile.read.side_effect = failed * 2 + [.8, .7, .5]
    farm = Farmer(controller, profile, FarmConfig(rest="I"), lambda _: None)
    assert farm.observe() == (.8, .7, .5)
    assert controller.capture.call_count == 3
    controller.key.assert_called_once_with("W", 100)


def test_low_mp_finishes_mob_before_rest():
    farm, controller, state = harness(pickup_loot=False)
    state["mp"] = .05
    original_wait = controller.wait.side_effect

    def press(key, ms):
        if key == "4":
            state["target"] = 0.
        elif key == "I":
            assert state["target"] == 0.

    def wait(seconds):
        original_wait(seconds)
        if farm.sitting:
            state["mp"] = 1.

    controller.key.side_effect = press
    controller.wait.side_effect = wait
    farm.run()
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys == ["3", "4", "I", "I"]


def test_lost_target_after_heal_does_not_allow_rest():
    farm, controller, state = harness()
    state.update(hp=.4, mp=.05)
    controller.key.side_effect = lambda key, ms: state.update(hp=1., target=None)
    assert farm.care()
    assert not farm.care()
    assert [call.args[0] for call in controller.key.call_args_list] == ["2"]


def test_finishing_requires_confirmed_death_before_loot():
    farm, controller, state = harness(combat_timeout=2, approach_delay=.2)
    state["target"] = .05
    with pytest.raises(RuntimeError, match="не добита"):
        farm.run()
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert "5" not in keys
    assert "I" not in keys
