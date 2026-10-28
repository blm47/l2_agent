from pathlib import Path
from unittest.mock import Mock

import pytest

from hid_pilot.telegram_notifications import _VideoUpload
from hid_pilot.telegram_video import MAX_VIDEO_BYTES, prepare_video


def test_missing_ffmpeg_does_not_modify_original_or_start_process(tmp_path, monkeypatch):
    monkeypatch.setattr("hid_pilot.telegram_video.shutil.which", lambda _: None)
    monkeypatch.setattr("hid_pilot.telegram_video.user_data_dir", lambda: tmp_path)
    run = Mock()
    monkeypatch.setattr("hid_pilot.telegram_video.subprocess.run", run)
    source = tmp_path / "original.mkv"
    source.write_bytes(b"original")
    with pytest.raises(ValueError, match="FFmpeg"):
        prepare_video(source, tmp_path / "copy.mp4")
    run.assert_not_called()
    assert source.read_bytes() == b"original"


@pytest.mark.parametrize("size,accepted", [(0, False), (100, True), (MAX_VIDEO_BYTES + 1, False)])
def test_conversion_checks_size_and_bounds_duration_resources_and_time(tmp_path, monkeypatch, size, accepted):
    monkeypatch.setattr("hid_pilot.telegram_video.shutil.which", lambda _: "ffmpeg")
    source, destination = tmp_path / "original.mkv", tmp_path / "copy.mp4"
    source.write_bytes(b"original")

    def encode(command, **kwargs):
        assert command[command.index("-sseof") + 1] == "-120"
        assert float(command[command.index("-t") + 1]) == 60
        assert "setpts=(PTS-STARTPTS)/2" in command[command.index("-vf") + 1]
        assert command[command.index("-r") + 1] == "30"
        assert command[command.index("-filter_threads") + 1] == "1"
        assert command[command.index("-threads") + 1] == "2"
        assert kwargs["timeout"] == 180
        assert kwargs["check"] is True
        with Path(command[-1]).open("wb") as output:
            output.truncate(size)

    monkeypatch.setattr("hid_pilot.telegram_video.subprocess.run", encode)
    if accepted:
        prepare_video(source, destination)
    else:
        with pytest.raises(ValueError):
            prepare_video(source, destination)
    assert source.read_bytes() == b"original"


def test_multipart_streams_large_video_without_a_single_full_file_read(tmp_path):
    source = tmp_path / "copy.mp4"
    source.write_bytes(b"v" * (2 * 1024 * 1024 + 123))
    upload = _VideoUpload(source, {"chat_id": "42"})
    chunks = list(upload)
    assert all(len(chunk) <= 1024 * 1024 for chunk in chunks)
    assert sum(map(len, chunks)) == upload.size
    assert b"".join(chunks[1:-1]) == source.read_bytes()
