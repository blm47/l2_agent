import io
import json
import os
import time
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from l2_agent.advisory import LocalChatProvider, SkillProposal
from l2_agent.advisory_panel import AdvisoryPanel
from l2_agent.world_state import WorldState


def response(action=None, **kwargs):
    proposal = {"action": action or {"skill": "ask_user", "question": "Какая цель?"},
                "rationale": "Состояние неизвестно", "confidence": 0.5}
    return json.dumps({"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(proposal), **kwargs}}], "usage": {"completion_tokens": 20}}).encode()


def test_provider_validates_and_sends_world_state(monkeypatch):
    calls = []

    def open_request(request, timeout):
        calls.append(json.loads(request.data))
        assert timeout == 60
        return io.BytesIO(response())

    monkeypatch.setattr("l2_agent.advisory.urlopen", open_request)
    result = LocalChatProvider("test").propose(WorldState(frame_id=42, timestamp=1), "Помоги")
    assert result.frame_id == 42
    assert result.proposal.action.skill == "ask_user"
    assert result.telemetry.completion_tokens == 20
    state = json.loads(calls[0]["messages"][1]["content"])["world_state"]
    assert state["player"]["hp"]["value"] is None
    assert calls[0]["response_format"]["type"] == "json_schema"


@pytest.mark.parametrize("action", [
    {"skill": "SendInput"}, {"skill": "rest", "key": "R"},
    {"skill": "ask_user"}, {"skill": "move_to_screen_point", "x": 2},
])
def test_rejects_unknown_or_malformed_skills(monkeypatch, action):
    monkeypatch.setattr("l2_agent.advisory.urlopen", lambda *a, **k: io.BytesIO(response(action)))
    with pytest.raises(ValidationError):
        LocalChatProvider("test").propose(WorldState(frame_id=0, timestamp=1), "Цель")


@pytest.mark.parametrize("raw", [b"not json", response(tool_calls=[{}]), b"x" * 262145,
                                    response().replace(b'"stop"', b'"length"')],
                         ids=["invalid-json", "tool-call", "oversized", "truncated"])
def test_rejects_bad_responses(monkeypatch, raw):
    monkeypatch.setattr("l2_agent.advisory.urlopen", lambda *a, **k: io.BytesIO(raw))
    with pytest.raises(ValueError):
        LocalChatProvider("test").propose(WorldState(frame_id=0, timestamp=1), "Цель")


@pytest.mark.parametrize("confidence", ["0.5", float("nan"), 2, -1])
def test_strict_confidence(confidence):
    with pytest.raises(ValidationError):
        SkillProposal(action={"skill": "rest"}, rationale="Причина", confidence=confidence)


def test_panel_requires_fresh_snapshot_and_handles_failure():
    app = QApplication.instance() or QApplication([])
    provider = Mock()
    state = WorldState(frame_id=1, timestamp=0)
    panel = AdvisoryPanel(provider, lambda: state)
    try:
        panel.goal.setText("Цель")
        panel.request_advice()
        provider.propose.assert_not_called()
        state = WorldState(frame_id=2, timestamp=time.monotonic())
        provider.propose.side_effect = ValueError("Ошибка")
        panel.request_advice()
        panel.request_advice()
        deadline = time.monotonic() + 2
        while panel.busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert not panel.busy
        assert "отклонён" in panel.status.text()
        provider.propose.assert_called_once()
    finally:
        panel.timer.stop()
        panel.close()
