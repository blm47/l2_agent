import threading
from unittest.mock import Mock

import numpy as np
import pytest

from l2_agent.capture import CapturedFrame
from l2_agent.detector import DetectorWorker, decode_output, prepare_image
from l2_agent.geometry import ClientRect
from l2_agent.world_state import ObjectObservation


def test_letterbox_rgb_and_class_specific_nms():
    rgb = np.full((100, 200, 3), (255, 0, 0), dtype=np.uint8)
    tensor, gain, left, top = prepare_image(rgb, 960)
    assert (gain, left, top) == (4.8, 0, 240)
    assert tensor[0, 0, 480, 480] == 1
    assert tensor[0, 2, 480, 480] == 0
    rows = np.array([[480, 480, 480, 240, .9, .1],
                     [480, 480, 480, 240, .8, .1],
                     [480, 480, 480, 240, .1, .95]], dtype=np.float32)
    objects = decode_output(rows.T[None], 200, 100, gain, left, top, ("a", "b"))
    assert [o.kind for o in objects] == ["b", "a"]
    assert objects[0].box.model_dump() == pytest.approx(dict(x1=.25, x2=.75, y1=.25, y2=.75))


def test_invalid_output_and_empty_predictions():
    with pytest.raises(ValueError):
        decode_output(np.zeros((1, 4, 3)), 200, 100, 1, 0, 0, ("mob",))
    assert decode_output(np.zeros((1, 5, 3)), 200, 100, 1, 0, 0, ("mob",)) == ()
    assert decode_output(np.full((1, 5, 3), np.nan), 200, 100, 1, 0, 0, ("mob",)) == ()
    with pytest.raises(ValueError):
        ObjectObservation(ready=True)


def make_frame(sequence=1, timestamp=100):
    return CapturedFrame(sequence=sequence, timestamp=timestamp,
                         rect=ClientRect(left=0, top=0, right=20, bottom=10),
                         rgb=np.zeros((10, 20, 3), dtype=np.uint8))


def test_worker_bounded_and_discards_previous_window(monkeypatch):
    monkeypatch.setattr("l2_agent.detector.time.monotonic", lambda: 100)
    started, release = threading.Event(), threading.Event()
    def detect(rgb):
        started.set()
        assert release.wait(2)
        return ()
    provider = Mock()
    provider.detect.side_effect = detect
    worker = DetectorWorker(provider)
    try:
        worker.observe(make_frame(), True, (1, 2))
        assert started.wait(1)
        for _ in range(10):
            assert not worker.observe(make_frame(2), True, (3, 4)).ready
        assert provider.detect.call_count == 1
        release.set()
        worker._thread.join(1)
        assert not worker.observe(make_frame(2), True, (3, 4)).ready
    finally:
        release.set()
        worker.close()


def test_worker_source_expiry_disable_and_empty_success(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("l2_agent.detector.time.monotonic", lambda: now[0])
    provider = Mock()
    provider.detect.return_value = ()
    worker = DetectorWorker(provider)
    worker.observe(make_frame(), True, (1, 2))
    worker._thread.join(1)
    result = worker.observe(make_frame(), True, (1, 2))
    assert result.ready and result.objects == () and result.frame_id == 1
    assert provider.detect.call_count == 1
    now[0] = 101.1
    assert not worker.observe(make_frame(), True, (1, 2)).ready
    assert not worker.observe(make_frame(timestamp=101.1), False, (1, 2)).ready
    worker.close()
    assert not worker.observe(make_frame(timestamp=101.1), True, (1, 2)).ready


def test_worker_error_backoff(monkeypatch):
    monkeypatch.setattr("l2_agent.detector.time.monotonic", lambda: 100)
    provider = Mock()
    provider.detect.side_effect = RuntimeError("test")
    worker = DetectorWorker(provider)
    worker.observe(make_frame(), True, (1, 2))
    worker._thread.join(1)
    result = worker.observe(make_frame(2), True, (1, 2))
    assert not result.ready and "Ошибка" in result.reason
    assert provider.detect.call_count == 1
    worker.close()
