import pytest

from hid_pilot.bars import BarProfile, BarBox
from hid_pilot.farm_ee import EEConfig, EEDecision
from hid_pilot.system_chat import ChatMessage
from test_farm_ee import runtime, mana_reserve_state


def chat_runtime(tmp_path, monkeypatch):
    state = mana_reserve_state(own_mp=1., members_mp=(.01, .1))
    state = state.model_copy(update={"selected_name": "A", "selected_confidence": .99})
    farmer, now = runtime(tmp_path, monkeypatch, state)
    farmer.own = BarProfile(width=100, height=100, system_chat_enabled=True,
                           system_chat=BarBox(x1=0, y1=0, x2=1, y2=1))
    # Тестовая фраза задаётся явно, без утверждения о формулировках сервера.
    farmer.config.mana_block_messages = ("TEST MP RESTRICTION",)
    farmer.observer.chat.chat_ready.return_value = True
    farmer.observer.chat.take_chat_events.return_value = []
    farmer.consume_mana_chat(state)
    return farmer, now, state


@pytest.mark.parametrize("skill", ["mana", "gain"])
def test_fresh_configured_error_blocks_confirmed_recipient(tmp_path, monkeypatch, skill):
    farmer, now, state = chat_runtime(tmp_path, monkeypatch)
    farmer.record_mana_chat_attempt(EEDecision(skill=skill, target="@slot1"), state, 10.)
    farmer.observer.chat.take_chat_events.return_value = [ChatMessage("unknown", "TEST MP RESTRICTION", 10.5)]
    now[0] = 11.
    farmer.consume_mana_chat(state)
    assert not farmer.ee.can_receive_mana("@slot1", state)
    assert farmer.ee.can_receive_mana("@slot2", state)
    assert farmer.ee.decide(state.model_copy(update={"at": 11.}), 11.) == EEDecision(skill="mana", target="@slot2")
    farmer.ee.decide(state.model_copy(update={"at": 72.}), 72.)
    assert not farmer.ee.can_receive_mana("@slot1", state.model_copy(update={"at": 72.}))


@pytest.mark.parametrize("reason", ["no_attempt", "stale", "before_command", "wrong_message", "pause",
                                    "revision", "ambiguous", "no_name", "no_baseline", "not_configured"])
def test_uncertain_chat_does_not_exclude_recipient(tmp_path, monkeypatch, reason):
    farmer, now, state = chat_runtime(tmp_path, monkeypatch)
    if reason == "no_name":
        state = state.model_copy(update={"members": tuple(m.model_copy(update={"name": None}) for m in state.members)})
    if reason == "no_baseline":
        farmer.observer.chat.chat_ready.return_value = False
    if reason != "no_attempt":
        farmer.record_mana_chat_attempt(EEDecision(skill="mana", target="@slot1"), state, 10.)
    if reason == "ambiguous":
        farmer.mana_chat_attempts.append((10.1, "B", 0, 0))
    if reason == "not_configured":
        farmer.config.mana_block_messages = ()
    if reason == "pause":
        farmer.controller.pause_generation = 1
    if reason == "revision":
        state = state.model_copy(update={"profile_revision": 1})
    now[0] = 15. if reason == "stale" else 11.
    farmer.observer.chat.take_chat_events.return_value = [ChatMessage(
        "unknown", "Other MP error" if reason == "wrong_message" else "TEST MP RESTRICTION",
        9.5 if reason == "before_command" else 10.5)]
    farmer.consume_mana_chat(state)
    assert not farmer.ee.mana_chat_blocked_names


def test_message_settings_are_exact_and_opt_in():
    assert "Recharge cannot be used due to unsuitable terms" in EEConfig().mana_block_messages
    assert EEConfig(mana_block_messages=()).mana_block_messages == ()
    assert EEConfig(mana_block_messages="One phrase|Another phrase").mana_block_messages == (
        "One phrase", "Another phrase")


def test_chat_block_suppresses_gain_for_recipient_only(tmp_path, monkeypatch):
    farmer, _, state = chat_runtime(tmp_path, monkeypatch)
    farmer.ee.mana_chat_blocked_names.add("a")
    farmer.ee.ready_at["buff:gain:self"] = 9999
    farmer.ee.ready_at.update(mana=9999, clarity=9999, shock=9999, kiss=9999)
    assert farmer.ee.decide(state, 10.) == EEDecision(skill="gain", target="@slot2")


@pytest.mark.parametrize("text", ["Rechardge cannot be used to unsituable terns",
                                  "RECHARDGE  cannot be used to unsituable terns.",
                                  "Recharge cannot be used due to unsuitable terms.",
                                  "Rechargecannotbeused dduetounsuitable eterms.",
                                  "RECHARGE  cannot be used due to unsuitable terms"])
def test_user_provided_message_activates_default_rule(tmp_path, monkeypatch, text):
    from hid_pilot.system_chat import parse_message

    farmer, now, state = chat_runtime(tmp_path, monkeypatch)
    farmer.config.mana_block_messages = EEConfig().mana_block_messages
    farmer.record_mana_chat_attempt(EEDecision(skill="mana", target="@slot1"), state, 10.)
    event = parse_message(text, 10.5)
    assert event.kind == "mana_target_block"
    farmer.observer.chat.take_chat_events.return_value = [event]
    now[0] = 11.
    farmer.consume_mana_chat(state)
    assert not farmer.ee.can_receive_mana("@slot1", state)
    assert farmer.ee.can_receive_mana("@slot2", state)
