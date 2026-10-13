from unittest.mock import Mock

from test_healer import snapshot
from test_healer_observer import profiles, scene
from test_healer_runtime import harness

from hid_pilot.auto_numbers import NumberCandidate
from hid_pilot.auto_party import PartyCandidate
from hid_pilot.bars import BarBox
from hid_pilot.healer import HealDecision
from hid_pilot.healer_observer import HealerReader
from hid_pilot.numbers import parse_numbers
from hid_pilot.session_perception import SessionPerception


def test_own_stats_buffer_expires_after_one_second_without_extending_on_miss():
    reader = HealerReader(*profiles())
    image = scene()
    assert reader.read(image, 1).hp == 1
    image[700:727] = 0
    assert reader.read(image, 1.5).hp == 1
    assert reader.read(image, 2).hp == 1
    assert reader.read(image, 2.01).hp is None
    assert reader.recovery.own_pending


def test_background_fast_read_does_not_run_own_ocr(monkeypatch):
    own, party = profiles()
    own.numbers = own.bars.copy()
    own.bars.clear()
    reader = HealerReader(own, party)
    reader.background_resources = True
    ocr = Mock()
    ocr.read.side_effect = AssertionError("Быстрое наблюдение не должно ждать OCR")
    monkeypatch.setattr("hid_pilot.numbers.shared_numbers", lambda: ocr)
    assert reader.read(scene(), 1).hp is None
    ocr.read.assert_not_called()


def test_roster_change_triggers_background_search_in_session_region(monkeypatch):
    own, party = profiles()
    recovery = SessionPerception(own, party)
    recovery.observe(snapshot(at=0, party=(1., 1., 1.)))
    recovery.observe(snapshot(at=.1, party=(1., 1.)))
    recovery.observe(snapshot(at=.7, party=(1., 1.)))
    assert recovery.party_pending
    finder = Mock(return_value=[PartyCandidate(profile=party, evidence="OCR")])
    monkeypatch.setattr("hid_pilot.session_perception.find_party", finder)
    updated, _ = recovery.find(scene(), own, 1)
    assert updated == party
    assert finder.call_args.args[2] == recovery.party_region


def test_temporarily_unreadable_names_do_not_trigger_repeated_roster_search():
    own, party = profiles()
    recovery = SessionPerception(own, party)
    initial = snapshot(at=0)
    recovery.observe(initial)
    for at in (.2, .8, 1.4):
        missing = initial.model_copy(update={"at": at, "members": tuple(member.model_copy(update={"name": None})
                                                                           for member in initial.members)})
        recovery.observe(missing)
    assert not recovery.party_pending
    assert recovery.signature == ((1, "A"), (2, "B"))


def test_ambiguous_party_search_keeps_old_profile_and_retries(monkeypatch):
    own, party = profiles()
    recovery = SessionPerception(own, party)
    recovery.party_pending = True
    finder = Mock(return_value=[PartyCandidate(profile=party, evidence="A"), PartyCandidate(profile=party, evidence="B")])
    monkeypatch.setattr("hid_pilot.session_perception.find_party", finder)
    assert recovery.find(scene(), own, 0) == (None, None)
    assert recovery.party_pending
    recovery.find(scene(), own, 1)
    assert finder.call_count == 1
    recovery.find(scene(), own, 2)
    assert finder.call_count == 2


def test_background_search_cannot_renumber_second_member_as_first(monkeypatch):
    own, party = profiles()
    recovery = SessionPerception(own, party)
    recovery.party_pending = True
    shifted = party.model_copy(update={kind: party.box(kind, 1) for kind in ("name", "cp", "hp", "mp")})
    monkeypatch.setattr("hid_pilot.session_perception.find_party", lambda *args: [PartyCandidate(profile=shifted, evidence="OCR")])
    assert recovery.find(scene(), own, 0) == (None, None)
    assert recovery.party_pending


def test_cancel_unneeded_cast_is_opt_in_and_preserves_cooldown(monkeypatch, tmp_path):
    healer, controller, _observer, initial, _now = harness(monkeypatch, tmp_path)
    decision = HealDecision(kind="cast", skill="major", target="@slot1", reason="HP ниже порога")
    healer.active_cast = decision, 15., False
    healthy = initial.model_copy(update={"members": (initial.members[0].model_copy(update={"hp": 1.}),)})
    healer.policy.ingest(healthy)
    healer.policy.ready_at["major"] = 12
    assert not healer.cancel_cast_if_unneeded(healthy, 10)
    controller.key.assert_not_called()
    healer.config.cancel_unneeded_casts = True
    assert healer.cancel_cast_if_unneeded(healthy, 10)
    controller.key.assert_called_once_with("ESC", 100)
    assert healer.policy.ready_at["major"] == 12


def test_needed_or_unknown_health_never_cancels_cast(monkeypatch, tmp_path):
    healer, controller, _observer, initial, _now = harness(monkeypatch, tmp_path)
    healer.config.cancel_unneeded_casts = True
    healer.active_cast = HealDecision(kind="cast", skill="major", target="@slot1", reason="тест"), 15., False
    assert not healer.cancel_cast_if_unneeded(initial, 10)
    assert not healer.cancel_cast_if_unneeded(initial.model_copy(update={"hp": None}), 10)
    controller.key.assert_not_called()


def test_own_background_search_maps_crop_coordinates_back_to_window(monkeypatch):
    own, party = profiles()
    recovery = SessionPerception(own, party)
    recovery.own_pending = True
    boxes = [BarBox(x1=.2, x2=.6, y1=.1 + index * .2, y2=.2 + index * .2) for index in range(3)]
    finder = Mock(return_value=[NumberCandidate(box=box, numbers=parse_numbers("50/100", .99), kind=kind, evidence="OCR")
                               for kind, box in zip(("cp", "hp", "mp"), boxes)])
    monkeypatch.setattr("hid_pilot.session_perception.find_numbers", finder)
    _, updated = recovery.find(scene(), own, 0)
    assert updated is not None and set(updated.numbers) == {"hp", "mp", "cp"}
    for box in updated.numbers.values():
        assert recovery.own_region.x1 <= box.x1 < box.x2 <= recovery.own_region.x2
        assert recovery.own_region.y1 <= box.y1 < box.y2 <= recovery.own_region.y2
    assert not recovery.own_pending


def test_new_ocr_revision_restores_monitoring_of_previously_excluded_slots():
    from hid_pilot.healer import HealerConfig, HealPolicy

    policy = HealPolicy(HealerConfig(), 0)
    policy.ingest(snapshot(at=1))
    policy.ignored_slots = {2}
    policy.ingest(snapshot(at=1.2).model_copy(update={"profile_revision": 1}))
    assert not policy.ignored_slots
    assert policy.expected_slots == {1, 2}


def test_skill_log_includes_trigger_and_key(monkeypatch, tmp_path):
    healer, _controller, _observer, state, _now = harness(monkeypatch, tmp_path)
    decision = healer.policy.decide(state, 10)
    healer.execute(decision, state)
    messages = [call.args[0] for call in healer.notify.call_args_list]
    assert any("Событие →" in message and decision.reason in message and "HP=" in message for message in messages)
