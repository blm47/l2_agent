import json
import logging
import threading
import urllib.request
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import pytest

from hid_pilot.telegram_notifications import PlayerDied
from test_replay import prepared, finish
from test_telegram_notifications import configured, finished, wait_for_delivery


def fake_video(monkeypatch):
    def convert(source, destination):
        destination.write_bytes(b"mp4-data")

    convert = Mock(side_effect=convert)
    monkeypatch.setattr("hid_pilot.telegram_notifications.prepare_video", convert)
    return convert


def response(body):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.read.return_value = json.dumps(body).encode()
    return result


def test_video_upload_is_next_message_and_replies_to_its_alert_over_real_http(tmp_path, monkeypatch):
    notifier = configured(tmp_path)
    requests = []
    alert_sent = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.path, dict(self.headers), body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "result": {"message_id": 100 + len(requests)}}).encode())
            if len(requests) == 1:
                alert_sent.set()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    original_open = urllib.request.urlopen

    def local_open(request, **kwargs):
        local = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/" + request.full_url.rsplit("/", 1)[1],
                                       data=request.data, headers=dict(request.header_items()), method="POST")
        return original_open(local, **kwargs)

    monkeypatch.setattr("hid_pilot.telegram_notifications.urllib.request.urlopen", local_open)
    fake_video(monkeypatch)
    clip = Future()
    source = tmp_path / "death.mkv"
    source.write_bytes(b"original-video")
    try:
        notifier.notify_death(PlayerDied("duo.pilot", "Support", "Саппорт"), video=clip)
        assert alert_sent.wait(1)
        notifier.notify_stopped(finished())
        assert len(requests) == 1
        clip.set_result(source)
        wait_for_delivery(notifier)
        assert [item[0] for item in requests] == ["/sendMessage", "/sendVideo", "/sendMessage"]
        _, headers, body = requests[1]
        assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
        assert int(headers["Content-Length"]) == len(body)
        assert b'name="reply_parameters"' in body
        assert b'"message_id": 101' in body
        assert b'"allow_sending_without_reply": false' in body
        assert b'Content-Type: video/mp4' in body
        assert "ускорено ×2".encode("utf-8") in body
        assert b'mp4-data' in body
        assert source.read_bytes() == b"original-video"
    finally:
        if not clip.done():
            clip.set_result(None)
        wait_for_delivery(notifier)
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.mark.parametrize("outcome", ["cancelled", "disabled", "no_message_id", "alert_failed", "encode_failed"])
def test_missing_or_disabled_video_never_creates_unthreaded_upload(tmp_path, monkeypatch, caplog, outcome):
    notifier = configured(tmp_path)
    if outcome == "disabled":
        with notifier.config_path.open("a", encoding="utf-8") as config:
            config.write("send_death_video = false\n")
    body = {"ok": True, "result": {"message_id": 42}}
    if outcome == "no_message_id":
        body = {"ok": True}
    urlopen = Mock(return_value=response(body))
    if outcome == "alert_failed":
        urlopen.side_effect = OSError("https://api.telegram.org/bot12345:TEST_SECRET/sendMessage")
    monkeypatch.setattr("hid_pilot.telegram_notifications.urllib.request.urlopen", urlopen)
    convert = fake_video(monkeypatch)
    if outcome == "encode_failed":
        convert.side_effect = OSError("TEST_SECRET")
    source = tmp_path / "death.mkv"
    source.write_bytes(b"original")
    clip = Future()
    clip.set_result(None if outcome == "cancelled" else source)
    with caplog.at_level(logging.WARNING, logger="hid_pilot.telegram"):
        notifier.notify_death(PlayerDied("duo.pilot", "Support"), video=clip)
        wait_for_delivery(notifier)
    assert urlopen.call_count == 1
    assert "TEST_SECRET" not in caplog.text
    assert source.read_bytes() == b"original"
    if outcome != "encode_failed":
        convert.assert_not_called()


def test_video_uses_same_chat_and_token_as_alert_if_config_changes(tmp_path, monkeypatch):
    notifier = configured(tmp_path)
    alert_sent = threading.Event()
    captured = []

    def send(request, **kwargs):
        captured.append(request)
        if request.full_url.endswith("sendMessage"):
            alert_sent.set()
        return response({"ok": True, "result": {"message_id": 77}})

    monkeypatch.setattr("hid_pilot.telegram_notifications.urllib.request.urlopen", send)
    fake_video(monkeypatch)
    source = tmp_path / "death.mkv"
    source.write_bytes(b"original")
    clip = Future()
    notifier.notify_death(PlayerDied("farm.pilot", "MA"), video=clip)
    assert alert_sent.wait(1)
    notifier.config_path.write_text('[telegram]\ntoken = "67890:OTHER"\nchat_id = 9999\n', "utf-8")
    clip.set_result(source)
    wait_for_delivery(notifier)
    assert len(captured) == 2
    assert captured[1].full_url.endswith("12345:TEST_SECRET/sendVideo")
    assert b"-100123456" in b"".join(captured[1].data.prefix.splitlines())


@pytest.mark.parametrize("cancel", [False, True])
def test_replay_completes_video_future_on_save_or_stop(tmp_path, cancel):
    replay, client, clock, wait, video, status = prepared(tmp_path)
    completion = Future()
    replay.trigger("Смерть", death=True, completion=completion)
    assert wait.entered.wait(1)
    assert not completion.done()
    if cancel:
        replay.trigger("STOP", final=True)
    finish(replay, wait)
    assert completion.result() == (None if cancel else video)
    if not cancel:
        replay.trigger("STOP", final=True)
        replay.worker.join(3)


def test_nearby_deaths_keep_separate_clips_and_alert_references(tmp_path):
    replay, client, clock, wait, video, status = prepared(tmp_path)
    first, second = Future(), Future()
    replay.trigger("Смерть саппорта", death=True, completion=first)
    replay.trigger("Смерть МА", death=True, completion=second)
    replay.trigger("Текущая цель добита", final=True, preserve_death=True)
    finish(replay, wait)
    assert first.result(timeout=1) == second.result(timeout=1) == video
    assert client.save.call_count == 2
    replay.worker.join(3)
    client.stop.assert_called_once()


def test_stop_completes_all_queued_video_futures_without_saving(tmp_path):
    replay, client, clock, wait, video, status = prepared(tmp_path)
    first, second = Future(), Future()
    replay.trigger("Смерть саппорта", death=True, completion=first)
    assert wait.entered.wait(1)
    replay.trigger("Смерть МА", death=True, completion=second)
    replay.close()
    replay.worker.join(3)
    assert first.result(timeout=1) is second.result(timeout=1) is None
    client.save.assert_not_called()


def test_failed_replay_completes_waiting_delivery_without_video(tmp_path):
    replay, client, clock, wait, video, status = prepared(tmp_path)
    client.save.side_effect = OSError("Failed")
    completion = Future()
    replay.trigger("Смерть", death=True, completion=completion)
    finish(replay, wait)
    assert completion.result() is None


def test_replay_queue_is_bounded_and_all_cancelled_waiters_are_released(tmp_path):
    replay, client, clock, wait, video, status = prepared(tmp_path)
    first = Future()
    replay.trigger("Смерть", death=True, completion=first)
    assert wait.entered.wait(1)
    queued = [Future() for _ in range(9)]
    for completion in queued:
        replay.trigger("Следующая смерть", death=True, completion=completion)
    assert len(replay.queued_incidents) == 8
    assert queued[-1].result(timeout=1) is None
    replay.trigger("STOP", final=True)
    replay.worker.join(3)
    assert first.result(timeout=1) is None
    assert all(completion.result(timeout=1) is None for completion in queued)
    client.save.assert_not_called()
