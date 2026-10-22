import json
import logging
import threading
from unittest.mock import Mock

import pytest

from hid_pilot.telegram_notifications import PlayerDied, TelegramNotifier, ScriptStopped, DEFAULT_CONFIG


def configured(tmp_path):
    path = tmp_path / "telegram.toml"
    notifier = TelegramNotifier(path)
    path.write_text('[telegram]\ntoken = "12345:TEST_SECRET"\nchat_id = -100123456\n', encoding="utf-8")
    return notifier


def finished():
    return ScriptStopped("farm_ee.pilot", "Тестовое окно", "STOP", 3661)


def wait_for_delivery(notifier):
    worker = notifier.worker
    if worker is not None:
        worker.join(timeout=2)
        assert not worker.is_alive()


def test_template_is_created_and_existing_config_is_not_overwritten(tmp_path):
    path = tmp_path / "telegram.toml"
    TelegramNotifier(path)
    assert path.read_text("utf-8") == DEFAULT_CONFIG.lstrip()
    path.write_text("user settings", encoding="utf-8")
    TelegramNotifier(path)
    assert path.read_text("utf-8") == "user settings"


def test_send_uses_json_utf8_and_default_stop_notification(tmp_path, monkeypatch):
    notifier = configured(tmp_path)
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b'{"ok":true}'
    urlopen = Mock(return_value=response)
    monkeypatch.setattr("hid_pilot.telegram_notifications.urllib.request.urlopen", urlopen)
    notifier.notify_stopped(finished())
    wait_for_delivery(notifier)
    urlopen.assert_called_once()
    request = urlopen.call_args.args[0]
    assert request.full_url == "https://api.telegram.org/bot12345:TEST_SECRET/sendMessage"
    assert request.get_method() == "POST"
    payload = json.loads(request.data)
    assert payload["chat_id"] == "-100123456"
    assert "Тестовое окно" in payload["text"] and "01:01:01" in payload["text"]
    assert urlopen.call_args.kwargs["timeout"] == 5


@pytest.mark.parametrize("setting", ["blank", "disabled", "stop_disabled", "invalid", "invalid_token"])
def test_unconfigured_or_disabled_notifications_never_use_network(tmp_path, monkeypatch, setting):
    notifier = configured(tmp_path)
    text = notifier.config_path.read_text("utf-8")
    if setting == "blank":
        text = DEFAULT_CONFIG
    elif setting == "disabled":
        text += "enabled = false\n"
    elif setting == "stop_disabled":
        text += "notify_on_stop = false\n"
    elif setting == "invalid_token":
        text = text.replace("12345:TEST_SECRET", "invalid")
    else:
        text = '[telegram]\ntoken = ["secret"]\n'
    notifier.config_path.write_text(text, encoding="utf-8")
    urlopen = Mock()
    monkeypatch.setattr("hid_pilot.telegram_notifications.urllib.request.urlopen", urlopen)
    notifier.notify_stopped(finished())
    wait_for_delivery(notifier)
    urlopen.assert_not_called()


def test_network_failure_does_not_leak_token_or_block_caller(tmp_path, monkeypatch, caplog):
    notifier = configured(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def blocked(*args, **kwargs):
        entered.set()
        release.wait(2)
        raise OSError("https://api.telegram.org/bot12345:TEST_SECRET/sendMessage")

    monkeypatch.setattr("hid_pilot.telegram_notifications.urllib.request.urlopen", blocked)
    with caplog.at_level(logging.WARNING, logger="hid_pilot.telegram"):
        try:
            notifier.notify_stopped(finished())
            assert entered.wait(1)
            assert notifier.worker.is_alive()
        finally:
            release.set()
            wait_for_delivery(notifier)
    assert "TEST_SECRET" not in caplog.text
    assert "sendMessage" not in caplog.text
    assert "Не удалось отправить" in caplog.text


@pytest.mark.parametrize("settings,expected", [
    ("", True), ("notify_on_stop = false\n", True),
    ("notify_on_death = false\n", False), ("enabled = false\n", False),
])
def test_death_notification_uses_existing_channel_and_independent_setting(tmp_path, monkeypatch, settings, expected):
    notifier = configured(tmp_path)
    with notifier.config_path.open("a", encoding="utf-8") as output:
        output.write(settings)
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b'{"ok":true}'
    urlopen = Mock(return_value=response)
    monkeypatch.setattr("hid_pilot.telegram_notifications.urllib.request.urlopen", urlopen)
    notifier.notify_death(PlayerDied("farm_ee.pilot", "LU4 — Healer"))
    wait_for_delivery(notifier)
    assert urlopen.call_count == int(expected)
    if expected:
        payload = json.loads(urlopen.call_args.args[0].data)
        assert payload["chat_id"] == "-100123456"
        assert "персонаж умер" in payload["text"]
        assert "farm_ee.pilot" in payload["text"] and "LU4 — Healer" in payload["text"]


def test_death_is_reported_once_until_resurrection():
    from hid_pilot.__main__ import ScriptNotifications
    telegram = Mock()
    notify = ScriptNotifications(Mock(), telegram, "farm.pilot", "LU4")
    notify.death()
    notify.death()
    telegram.notify_death.assert_called_once_with(PlayerDied("farm.pilot", "LU4"))
    notify.revived()
    notify.death()
    assert telegram.notify_death.call_count == 2
