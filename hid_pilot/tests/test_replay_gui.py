import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from hid_pilot.__main__ import MainWindow, ScriptNotifications
from hid_pilot.desktop import Target


@pytest.mark.parametrize("enabled,obs_error,debug,support_lost", [
    (False, False, False, False), (True, False, False, False), (True, False, True, False),
    (True, True, False, False), (True, False, False, True),
])
def test_recording_precedes_input_and_final_trigger_follows_release(
        monkeypatch, tmp_path, enabled, obs_error, debug, support_lost):
    app = QApplication.instance() or QApplication([])
    target = Target(hwnd=123, pid=42, title="Игра", rect=(0, 0, 800, 600))
    monkeypatch.setattr("hid_pilot.__main__.user_data_dir", lambda: tmp_path)
    monkeypatch.setattr("hid_pilot.__main__.HardStopHotkey", lambda *args: Mock(available=True))
    monkeypatch.setattr("hid_pilot.__main__.windows", lambda: [target])
    monkeypatch.setattr("hid_pilot.__main__.activate", lambda target: target)
    monkeypatch.setattr("hid_pilot.__main__.Desktop", Mock())
    monkeypatch.setattr("hid_pilot.__main__.PicoTransport", Mock())
    order = []
    controller = Mock()
    controller.start.side_effect = lambda: order.append("input")
    controller.close.side_effect = lambda: order.append("release")
    monkeypatch.setattr("hid_pilot.__main__.ActionController", Mock(return_value=controller))
    runner = Mock()
    if support_lost:
        from hid_pilot.duo import SupportDied
        runner.run.side_effect = SupportDied("Саппорт погиб: текущая цель добита")
    monkeypatch.setattr("hid_pilot.__main__.Runner", Mock(return_value=runner))
    window = MainWindow()
    replay = Mock(busy=False)
    def prepare():
        order.append("buffer")
        if obs_error:
            raise ValueError("OBS недоступен")
    replay.prepare.side_effect = prepare
    replay.trigger.side_effect = lambda *args, **kwargs: order.append("video")
    replay.close.return_value = True
    window.replay = replay
    window.cancel = Mock()
    window.cancel.wait.return_value = False
    window.cancel.is_set.return_value = False
    window.telegram = Mock()
    window.editor.setPlainText("wait 0.1")
    window.record_video.setChecked(enabled)
    try:
        window.launch(debug=debug)
        window.worker.join(3)
        assert not window.worker.is_alive()
        if not enabled:
            replay.prepare.assert_not_called()
            replay.trigger.assert_not_called()
            assert order == ["input", "release"]
        elif obs_error:
            controller.start.assert_not_called()
            replay.trigger.assert_not_called()
            assert order == ["buffer"]
        else:
            assert order == ["buffer", "input", "release", "video"]
            assert controller.debug_capture is debug
            expected = {"final": True, "preserve_death": True} if support_lost else {"final": True}
            assert replay.trigger.call_args.kwargs == expected
        app.processEvents()
    finally:
        window.close()


def test_checkbox_persists_and_pending_save_blocks_restart(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("hid_pilot.__main__.user_data_dir", lambda: tmp_path)
    monkeypatch.setattr("hid_pilot.__main__.HardStopHotkey", lambda *args: Mock(available=True))
    monkeypatch.setattr("hid_pilot.__main__.windows", lambda: [])
    window = MainWindow()
    assert not window.record_video.isChecked()
    window.record_video.setChecked(True)
    window.close()
    window = MainWindow()
    try:
        assert window.record_video.isChecked()
        window.replay = Mock(busy=True)
        window.launch()
        assert window.worker is None
        assert "сохранения видео" in window.status.text()
        app.processEvents()
    finally:
        window.close()


def test_death_hook_deduplicates_until_revived():
    replay = Mock()
    notifications = ScriptNotifications(Mock(), replay=replay)
    notifications.death()
    notifications.death()
    replay.trigger.assert_called_once_with("Смерть персонажа", death=True)
    notifications.revived()
    notifications.death()
    assert replay.trigger.call_count == 2


def test_support_and_farmer_deaths_are_independent_and_identify_the_window():
    from hid_pilot.telegram_notifications import PlayerDied

    replay, telegram = Mock(), Mock()
    notifications = ScriptNotifications(Mock(), telegram, "duo.pilot", "Окно МА",
                                        duo_mode=True, replay=replay, support_window="Окно саппорта")
    notifications.support_death()
    notifications.support_death()
    completion = telegram.notify_death.call_args.kwargs["video"]
    replay.trigger.assert_called_once_with("Смерть саппорта", death=True, completion=completion)
    telegram.notify_death.assert_called_once_with(PlayerDied("duo.pilot", "Окно саппорта", "Саппорт"), video=completion)
    notifications.death()
    assert replay.trigger.call_count == 2
    telegram.notify_death.assert_called_with(PlayerDied("duo.pilot", "Окно МА", "МА"),
                                            video=replay.trigger.call_args.kwargs["completion"])
    notifications.revived()
    notifications.support_death()
    assert replay.trigger.call_count == 2
    notifications.support_revived()
    notifications.support_death()
    assert replay.trigger.call_count == 3


def test_confirmed_support_death_saves_one_fifteen_second_clip_and_notifies(tmp_path):
    import json
    import numpy as np

    from hid_pilot.support_life import SupportLifeObserver
    from hid_pilot.telegram_notifications import PlayerDied
    from test_replay import prepared, finish

    replay, client, clock, wait, video, status = prepared(tmp_path)
    telegram = Mock()
    notifications = ScriptNotifications(Mock(), telegram, "duo.pilot", "МА", duo_mode=True,
                                        replay=replay, support_window="Саппорт")
    controller = Mock(pause_generation=0)
    controller.capture.return_value = np.zeros((220, 700, 3), np.uint8)
    observer = SupportLifeObserver(notifications.support_death, notifications.support_revived,
                                   reader=lambda image: 0, clock=lambda: 0)
    try:
        observer.observe(controller, None)
        assert wait.entered.wait(1)
        completion = telegram.notify_death.call_args.kwargs["video"]
        telegram.notify_death.assert_called_once_with(PlayerDied("duo.pilot", "Саппорт", "Саппорт"), video=completion)
        ma_video = video.with_name("ma.mkv")
        ma_video.write_bytes(b"video")
        client.save.side_effect = [video, ma_video]
        notifications.death()
        finish(replay, wait)
        assert completion.result() == video
        assert telegram.notify_death.call_args.kwargs["video"].result(timeout=1) == ma_video
        assert client.save.call_count == 2
        metadata = json.loads(video.with_suffix(".mkv.json").read_text("utf-8"))
        assert metadata["reason"] == "Смерть саппорта"
        assert metadata["requested_tail_seconds"] == 15
    finally:
        replay.trigger("STOP", final=True)
        replay.worker.join(3)


def test_stop_requests_video_discard_without_waiting_for_worker(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("hid_pilot.__main__.user_data_dir", lambda: tmp_path)
    monkeypatch.setattr("hid_pilot.__main__.HardStopHotkey", lambda *args: Mock(available=True))
    monkeypatch.setattr("hid_pilot.__main__.windows", lambda: [])
    window = MainWindow()
    window.replay = Mock()
    try:
        window.stop()
        assert window.cancel.is_set()
        window.replay.trigger.assert_called_once_with("STOP / hard-stop", final=True)
        app.processEvents()
    finally:
        window.close()
