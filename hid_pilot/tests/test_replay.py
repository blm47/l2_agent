import json
import base64
import hashlib
import threading
from unittest.mock import Mock

import pytest

from hid_pilot.replay import FarmReplay, OBSReplay, ReplayConfig


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class ControlledWait:
    def __init__(self, clock):
        self.clock = clock
        self.entered = threading.Event()
        self.release = threading.Event()
        self.seconds = None

    def wait(self, seconds):
        self.seconds = seconds
        self.entered.set()
        assert self.release.wait(3)
        self.clock.now += seconds

    def set(self):
        self.release.set()

    def clear(self):
        self.release.clear()


def prepared(tmp_path):
    clock = Clock()
    video = tmp_path / "replay.mkv"
    video.write_bytes(b"video")
    client = Mock(owned=True)
    client.save.return_value = video
    status = Mock()
    replay = FarmReplay(tmp_path / "obs.toml", status, lambda config: client, clock)
    replay.prepare()
    wait = ControlledWait(clock)
    replay.wake = wait
    return replay, client, clock, wait, video, status


def finish(replay, wait):
    worker = replay.worker
    wait.set()
    worker.join(3)
    assert not worker.is_alive()


def test_tail_dedup_and_metadata(tmp_path):
    replay, client, clock, wait, video, status = prepared(tmp_path)
    clock.now += 295
    replay.trigger("Смерть", death=True)
    assert wait.entered.wait(1)
    replay.trigger("Смерть", death=True)
    client.save.assert_not_called()
    assert replay.busy
    with pytest.raises(ValueError):
        replay.prepare()
    finish(replay, wait)
    replay.trigger("STOP", final=True)
    replay.worker.join(3)
    assert wait.seconds == 15
    client.save.assert_called_once()
    client.stop.assert_called_once()
    client.close.assert_called_once()
    data = json.loads(video.with_suffix(".mkv.json").read_text("utf-8"))
    assert data["reason"] == "Смерть"
    assert data["available_seconds_estimate"] == 120
    assert data["requested_history_seconds"] == 105
    assert data["post_event_seconds"] == 15
    assert data["requested_tail_seconds"] == 15
    assert any(call.args == ("Дописать после события: 15 секунд",) for call in status.call_args_list)
    assert not replay.busy
    assert video.with_suffix(".mkv.log").exists()


def test_close_discards_short_buffer_without_saving(tmp_path):
    replay, client, clock, wait, video, status = prepared(tmp_path)
    clock.now += 12
    replay.wake = threading.Event()
    replay.close()
    replay.worker.join(3)
    assert not replay.busy
    client.save.assert_not_called()
    assert not video.with_suffix(".mkv.json").exists()
    client.stop.assert_called_once()


def test_save_failure_is_not_success(tmp_path):
    replay, client, clock, wait, video, status = prepared(tmp_path)
    client.save.side_effect = TimeoutError("SECRET")
    replay.trigger("Смерть", death=True)
    finish(replay, wait)
    assert not video.with_suffix(".mkv.json").exists()
    assert all("Сохранено:" not in call.args[0] for call in status.call_args_list)
    assert all("SECRET" not in call.args[0] for call in status.call_args_list)
    assert not replay.busy
    client.stop.assert_called_once()


def test_failed_prepare_cleans_owned_buffer_without_secret(tmp_path):
    client = Mock(owned=True)
    client.start.side_effect = RuntimeError("SECRET")
    replay = FarmReplay(tmp_path / "obs.toml", Mock(), lambda config: client)
    with pytest.raises(ValueError, match="OBS") as error:
        replay.prepare()
    assert "SECRET" not in str(error.value)
    client.stop.assert_called_once()
    client.close.assert_called_once()
    assert not replay.busy


def test_death_can_be_followed_by_another_incident(tmp_path):
    replay, client, clock, wait, video, status = prepared(tmp_path)
    replay.trigger("Смерть", death=True)
    finish(replay, wait)
    assert wait.seconds == 15
    assert replay.busy
    client.stop.assert_not_called()
    wait.clear()
    replay.trigger("Смерть", death=True)
    finish(replay, wait)
    assert wait.seconds == 15
    assert client.save.call_count == 2
    replay.trigger("STOP", final=True)
    replay.worker.join(3)
    client.stop.assert_called_once()
    assert not replay.busy


@pytest.mark.parametrize("reason", ["STOP", "hard-stop", "Ошибка", "Лимит времени", "Сценарий завершён"])
def test_final_event_discards_buffer_without_tail(tmp_path, reason):
    replay, client, clock, wait, video, status = prepared(tmp_path)
    replay.trigger(reason, final=True)
    replay.worker.join(3)
    assert not replay.worker.is_alive()
    assert not replay.busy
    assert wait.seconds is None
    client.stop.assert_called_once()
    client.close.assert_called_once()
    client.save.assert_not_called()
    assert not video.with_suffix(".mkv.json").exists()
    assert not video.with_suffix(".mkv.log").exists()


@pytest.mark.parametrize("closing", [False, True])
def test_stop_or_close_cancels_pending_death_clip(tmp_path, closing):
    replay, client, clock, wait, video, status = prepared(tmp_path)
    replay.trigger("Смерть", death=True)
    assert wait.entered.wait(1)
    if closing:
        replay.close()
    else:
        replay.trigger("STOP", final=True)
    replay.worker.join(3)
    assert not replay.worker.is_alive()
    assert not replay.busy
    client.save.assert_not_called()
    client.stop.assert_called_once()
    assert not video.with_suffix(".mkv.json").exists()


def test_finishing_after_support_death_preserves_pending_fifteen_second_clip(tmp_path):
    replay, client, clock, wait, video, status = prepared(tmp_path)
    replay.trigger("Смерть саппорта", death=True)
    assert wait.entered.wait(1)
    replay.trigger("Текущая цель добита", final=True, preserve_death=True)
    client.save.assert_not_called()
    assert not replay.discard
    finish(replay, wait)
    client.save.assert_called_once()
    client.stop.assert_called_once()
    assert not replay.busy
    assert wait.seconds == 15


def test_manual_stop_still_cancels_clip_after_support_death_completion(tmp_path):
    replay, client, clock, wait, video, status = prepared(tmp_path)
    replay.trigger("Смерть саппорта", death=True)
    assert wait.entered.wait(1)
    replay.trigger("Текущая цель добита", final=True, preserve_death=True)
    replay.trigger("STOP", final=True)
    replay.worker.join(3)
    client.save.assert_not_called()
    client.stop.assert_called_once()
    assert not replay.busy


def test_finishing_after_saved_death_does_not_create_another_clip(tmp_path):
    replay, client, clock, wait, video, status = prepared(tmp_path)
    replay.trigger("Смерть саппорта", death=True)
    finish(replay, wait)
    replay.trigger("Текущая цель добита", final=True, preserve_death=True)
    replay.worker.join(3)
    client.save.assert_called_once()
    client.stop.assert_called_once()
    assert not replay.busy


def test_stop_during_obs_start_discards_after_activation(tmp_path):
    client = Mock(owned=True)
    entered, release = threading.Event(), threading.Event()

    def start():
        entered.set()
        assert release.wait(3)

    client.start.side_effect = start
    replay = FarmReplay(tmp_path / "obs.toml", Mock(), lambda config: client)
    prepare = threading.Thread(target=replay.prepare)
    prepare.start()
    try:
        assert entered.wait(1)
        replay.trigger("STOP", final=True)
        assert replay.worker is None
        client.stop.assert_not_called()
    finally:
        release.set()
        prepare.join(3)
    replay.worker.join(3)
    assert not prepare.is_alive() and not replay.worker.is_alive()
    assert not replay.busy
    client.stop.assert_called_once()
    client.save.assert_not_called()


def test_recording_can_restart_after_discard(tmp_path):
    replay, client, clock, wait, video, status = prepared(tmp_path)
    replay.trigger("STOP", final=True)
    first = replay.worker
    first.join(3)
    assert not replay.busy
    replay.prepare()
    replay.trigger("STOP", final=True)
    second = replay.worker
    second.join(3)
    assert first is not second
    assert not replay.busy and not second.is_alive()
    assert client.stop.call_count == 2
    client.save.assert_not_called()


def test_protocol_waits_for_event_and_checks_file(tmp_path):
    client = OBSReplay.__new__(OBSReplay)
    client.socket = Mock()
    client.saved = None
    video = tmp_path / "video.mkv"
    video.write_bytes(b"video")
    def send(payload):
        request = json.loads(payload)["d"]
        client.socket.recv.side_effect = [
            json.dumps({"op": 5, "d": {"eventType": "ReplayBufferSaved",
                       "eventData": {"savedReplayPath": str(video)}}}),
            json.dumps({"op": 7, "d": {"requestId": request["requestId"],
                       "requestStatus": {"result": True}}})]
    client.socket.send.side_effect = send
    assert client.save() == video
    video.unlink()
    with pytest.raises(ValueError, match="Файл"):
        client.save()


def test_existing_obs_buffer_is_never_stopped():
    client = OBSReplay.__new__(OBSReplay)
    client.owned = False
    client.request = Mock(return_value={"outputActive": True})
    with pytest.raises(ValueError, match="занят"):
        client.start()
    client.request.assert_called_once_with("GetReplayBufferStatus")


def test_obs_authentication_and_event_subscription(monkeypatch):
    socket = Mock()
    socket.recv.side_effect = [
        json.dumps({"op": 0, "d": {"authentication": {"salt": "salt", "challenge": "challenge"}}}),
        json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}),
    ]
    connect = Mock(return_value=socket)
    monkeypatch.setattr("websocket.create_connection", connect)
    client = OBSReplay(ReplayConfig(password="secret"))
    identify = json.loads(socket.send.call_args.args[0])
    secret = base64.b64encode(hashlib.sha256(b"secretsalt").digest()).decode()
    auth = base64.b64encode(hashlib.sha256((secret + "challenge").encode()).digest()).decode()
    assert identify == {"op": 1, "d": {"rpcVersion": 1, "eventSubscriptions": 64,
                                       "authentication": auth}}
    connect.assert_called_once_with("ws://127.0.0.1:4455", timeout=5)
    client.close()


def test_obs_checks_history_limits_before_start():
    client = OBSReplay.__new__(OBSReplay)
    client.owned = False
    client.request = Mock(side_effect=[{"outputActive": False}, {"parameterValue": "Advanced"},
                                       {"parameterValue": "20"}])
    with pytest.raises(ValueError, match="120"):
        client.start()
    assert "StartReplayBuffer" not in [call.args[0] for call in client.request.call_args_list]


def test_obs_start_waits_for_encoder_activation(monkeypatch):
    client = OBSReplay.__new__(OBSReplay)
    client.owned = False
    client.request = Mock(side_effect=[
        {"outputActive": False}, {"parameterValue": "Advanced"},
        {"parameterValue": "120"}, {"parameterValue": "512"}, {},
        {"outputActive": False}, {"outputActive": True},
    ])
    sleep = Mock()
    monkeypatch.setattr("hid_pilot.replay.time.sleep", sleep)
    client.start()
    assert client.owned
    sleep.assert_called_once_with(0.1)


def test_obs_start_timeout_is_bounded(monkeypatch):
    client = OBSReplay.__new__(OBSReplay)
    client.owned = False
    client.request = Mock(side_effect=[
        {"outputActive": False}, {"parameterValue": "Advanced"},
        {"parameterValue": "120"}, {"parameterValue": "512"}, {},
        {"outputActive": False},
    ])
    monkeypatch.setattr("hid_pilot.replay.time.monotonic", Mock(side_effect=[0, 11]))
    with pytest.raises(ValueError, match="не включился"):
        client.start()
    assert client.owned
