import pytest

from hid_pilot.party import PartyMember
from hid_pilot.target import TargetObservation
from test_duo import make_duo


def strong_duo(**config):
    duo, controller = make_duo(heal_key=None, buff_delay_minutes=100, **config)
    duo.clock = controller.active_time
    duo.deadline = 200.
    return duo, controller


def damage(duo, controller, loss=.04):
    duo.track_damage(None, 1.)
    controller.wait(.2)
    duo.track_damage(None, 1. - loss)


def select(duo, controller, *, name="Giant", maximum=10000, **target):
    duo.invalidate_target()
    duo.press(duo.config.next_target)
    duo.last_target = TargetObservation(
        at=controller.now, name=name, kind="monster", hp=.9, confidence=1., **target)
    duo.observation_details = {"hp": "OCR 900/1000", "target_hp": f"OCR 9000/{maximum}"}
    return duo.reject_unsafe_target()


def permit(duo, controller):
    for attempt in range(6):
        damage(duo, controller)
        assert select(duo, controller) is (attempt < 5)


def test_six_selections_allow_combat_and_support_for_current_target(caplog):
    duo, controller = strong_duo(aggro_key="2", damage_keys=("F7",), overhit_keys=("F8",),
                                 vampiric_key="F9")
    with caplog.at_level("INFO", logger="hid_pilot.farm"):
        permit(duo, controller)
    assert controller.events.count((1, "ESC")) == 5
    assert controller.events.count((1, duo.config.next_target)) == 6
    assert "6/6" in caplog.text and "HP-ограничение снято" in caplog.text
    # После начала отпора лечение пати не должно снова запрещать текущую цель.
    controller.wait(3.)
    duo.track_damage(None, 1.)
    for key in (duo.config.attack, "2", "F7", "F8", "F9"):
        assert duo.combat_key(key, 100)
    duo.track_support_attack(.9)
    duo.track_support_attack(.8)
    duo.support_pending = True
    duo.assist_support()
    assert (2, duo.config.support_key) in controller.events
    assert controller.events.count((1, "ESC")) == 5


@pytest.mark.parametrize("loss", [0., .029, .03])
def test_only_damage_at_or_above_configured_threshold_allows_exception(loss):
    duo, controller = strong_duo()
    for attempt in range(8):
        damage(duo, controller, loss)
        blocked = select(duo, controller)
        assert blocked is (loss < .03 or attempt != 5)


def test_repeated_guard_checks_and_observations_are_not_selections():
    duo, controller = strong_duo()
    damage(duo, controller)
    assert select(duo, controller)
    for _ in range(10):
        duo.last_target = TargetObservation(at=controller.now, name="Giant", kind="monster",
                                            hp=.9, confidence=1.)
        assert duo.is_unsafe_target()
        assert not duo.alive_target(.9)
        assert duo.reject_unsafe_target()
    assert duo.strong_aggressor_selections == 1


@pytest.mark.parametrize("interruption", ["name", "maximum", "unknown_name", "confidence", "player",
                                         "npc", "clan", "player_class", "dead", "zero", "missing_hp",
                                         "ordinary", "pause", "quiet", "stale", "unknown_resources"])
def test_interrupted_search_requires_six_new_matching_selections(interruption):
    duo, controller = strong_duo()
    for _ in range(5):
        damage(duo, controller)
        assert select(duo, controller)
    damage(duo, controller)
    duo.press(duo.config.next_target)
    changes = {
        "name": {"name": "Other"}, "unknown_name": {"name": None},
        "confidence": {"confidence": .5}, "player": {"kind": "player"},
        "npc": {"kind": "npc"}, "clan": {"clan": "Clan"},
        "player_class": {"player_class": "Hawkeye"}, "dead": {"death_marker": True},
        "zero": {"hp": 0.}, "missing_hp": {"hp": None},
    }.get(interruption, {})
    duo.last_target = TargetObservation.model_validate(
        {"at": controller.now, "name": "Giant", "kind": "monster", "hp": .9,
         "confidence": 1., **changes})
    maximum = {"maximum": 12000, "ordinary": 9000}.get(interruption, 10000)
    duo.observation_details = {"hp": "OCR 900/1000", "target_hp": f"OCR 8000/{maximum}"}
    if interruption == "pause":
        controller.pause_generation += 1
    elif interruption == "quiet":
        controller.wait(duo.config.damage_memory_seconds + .1)
        duo.track_damage(None, 1.)
    elif interruption == "stale":
        controller.wait(1.1)
    elif interruption == "unknown_resources":
        duo.on_observation(None, None, None, .9)
    duo.reject_unsafe_target()
    assert duo.strong_aggressor_allowed is None
    for attempt in range(6):
        damage(duo, controller)
        assert select(duo, controller) is (attempt < 5)


@pytest.mark.parametrize("interruption", ["new_selection", "pause", "name", "maximum", "death",
                                         "unknown", "player", "npc"])
def test_permission_is_revoked_after_target_change_or_pause(interruption):
    duo, controller = strong_duo()
    permit(duo, controller)
    old_target = duo.last_target
    if interruption == "new_selection":
        duo.invalidate_target()
        duo.press(duo.config.next_target)
        duo.last_target = old_target
    elif interruption == "pause":
        controller.pause_generation += 1
    elif interruption == "maximum":
        duo.observation_details["target_hp"] = "OCR 9000/12000"
    else:
        changes = {"name": {"name": "Other"}, "death": {"death_marker": True},
                   "unknown": {"name": None}, "player": {"kind": "player"},
                   "npc": {"kind": "npc"}}[interruption]
        duo.last_target = old_target.model_copy(update=changes)
    assert duo.is_unsafe_target()
    assert duo.strong_aggressor_allowed is None


@pytest.mark.parametrize("resource", ["support_hp", "summon_hp", "party_hp", "support_mp", "summon_mp", "party_cp"])
def test_party_hp_damage_counts_but_mp_and_cp_do_not(resource):
    duo, controller = strong_duo()
    duo.party_profile = object()
    resources = {"support_hp": 1., "summon_hp": 1., "support_mp": 1., "summon_mp": 1.}
    duo.support_resources = lambda image: resources.copy()
    duo.display_data = ({}, {}, [PartyMember(slot=3, hp=1., cp=1.)])
    for attempt in range(6):
        duo.track_damage(None, 1.)
        controller.wait(.2)
        if resource.startswith("party_"):
            value = 1. - .04 * (attempt + 1)
            duo.display_data = ({}, {}, [PartyMember(slot=3, **{resource[6:]: value})])
        else:
            resources[resource] -= .04
        duo.track_damage(None, 1.)
        assert select(duo, controller) is (attempt < 5 or not resource.endswith("_hp"))


def test_acquire_and_retaliation_use_real_observation_and_only_six_next_targets():
    duo, controller = strong_duo(aggro_key="2", target_delay=.2)
    selected = False
    hp = 1.
    last_at = controller.now
    original_key = controller.key

    def key(name, duration, **kwargs):
        nonlocal selected
        original_key(name, duration, **kwargs)
        if name == duo.config.next_target:
            selected = True
        elif name == "ESC":
            selected = False

    def capture():
        nonlocal hp, last_at
        hp -= .2 * (controller.now - last_at)
        last_at = controller.now
        duo.last_target = (TargetObservation(at=controller.now, name="Giant", kind="monster",
                                             hp=.9, confidence=1.) if selected else None)
        return hp, .9 if selected else None, False

    def read(image, name):
        duo.observation_details[name] = {
            "hp": "OCR 900/1000", "target_hp": "OCR 9000/10000"}.get(name, "полоса")
        return 1. if name == "mp" else image[0 if name == "hp" else 1]

    controller.key = key
    controller.capture = capture
    duo.profile.read = read
    duo.observe_ma()
    controller.wait(.2)
    assert duo.acquire() == .9
    keys = [key for role, key in controller.events if role == 1]
    assert keys.count(duo.config.next_target) == 6
    assert keys.count("ESC") == 5
    assert keys[-2:] == ["2", duo.config.attack]


def test_hp_exception_keeps_level_filter():
    duo, controller = strong_duo()
    permit(duo, controller)
    duo.display_data = ({"level": 50}, {}, [])
    duo.last_target = duo.last_target.model_copy(update={"level": 55})
    assert duo.reject_high_level_target()
    assert duo.strong_aggressor_allowed is None
