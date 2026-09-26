import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from l2_agent import capture
from l2_agent.geometry import ClientRect


def test_monitor_region_handles_negative_origin():
    monitor = ClientRect(left=-1920, top=-100, right=0, bottom=980)
    client = ClientRect(left=-1800, top=0, right=-100, bottom=800)
    assert capture.monitor_region(client, monitor) == (120, 100, 1820, 900)


@pytest.mark.parametrize("left,right", [(-2000, -100), (-1800, 1), (0, 100)])
def test_region_rejects_partial_or_other_monitor(left, right):
    with pytest.raises(ValueError):
        capture.monitor_region(
            ClientRect(left=left, top=0, right=right, bottom=800),
            ClientRect(left=-1920, top=0, right=0, bottom=1080),
        )


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("Capture worker не обновил состояние за 3 секунды")


@pytest.fixture
def worker_setup(monkeypatch):
    rect = ClientRect(left=10, top=20, right=30, bottom=30)
    manager = Mock()
    manager.snapshot.return_value = SimpleNamespace(rect=rect)
    backend = Mock()
    backend.grab.return_value = np.zeros((10, 20, 3), dtype=np.uint8)
    monkeypatch.setattr(capture, "GameWindowManager", lambda: manager)
    monkeypatch.setattr(capture, "BetterCamBackend", lambda: backend)
    worker = capture.CaptureWorker()
    yield worker, manager, backend
    assert worker.close()


def test_worker_latest_frame_and_disable(worker_setup):
    worker, _manager, backend = worker_setup
    worker.select((42, 123))
    wait_for(lambda: worker.latest().frame is not None)
    first = worker.latest().frame
    assert first.rgb.shape == (10, 20, 3)
    assert not first.rgb.flags.writeable
    assert not np.shares_memory(first.rgb, backend.grab.return_value)
    wait_for(lambda: worker.latest().frame.sequence > first.sequence)
    worker.select(None)
    assert worker.latest().frame is None
    wait_for(lambda: backend.close.called)
    assert worker.latest().fps == 0


def test_minimize_clears_preview(worker_setup):
    worker, manager, _backend = worker_setup
    worker.select((42, 123))
    wait_for(lambda: worker.latest().frame is not None)
    manager.snapshot.return_value = SimpleNamespace(rect=None)
    wait_for(lambda: worker.latest().message == "Окно свёрнуто")
    assert worker.latest().frame is None
    assert worker.latest().fps == 0


def test_resize_during_grab_discards_frame(worker_setup):
    worker, manager, backend = worker_setup
    old = manager.snapshot.return_value
    changed = SimpleNamespace(rect=ClientRect(left=0, top=0, right=25, bottom=10))
    manager.snapshot.side_effect = lambda *args: old if not backend.grab.called else changed
    worker.select((42, 123))
    wait_for(lambda: worker.latest().message == "Геометрия меняется")
    assert worker.latest().frame is None


def test_backend_error_clears_frame(worker_setup):
    worker, _manager, backend = worker_setup
    worker.select((42, 123))
    wait_for(lambda: worker.latest().frame is not None)
    backend.grab.side_effect = RuntimeError("DXGI недоступен")
    wait_for(lambda: "DXGI недоступен" in worker.latest().message)
    assert worker.latest().frame is None
    assert worker.latest().fps == 0


def test_old_generation_cannot_publish_after_stop(worker_setup):
    worker, _manager, _backend = worker_setup
    worker.select((42, 123))
    generation = worker._generation
    worker.select(None)
    worker._publish(generation, capture.CaptureStatus(message="Старый кадр"))
    assert worker.latest().message == "Preview выключен"


def test_backend_uses_monitor_relative_region_and_releases(monkeypatch):
    module = Mock()
    bounds = SimpleNamespace(left=-1920, top=0, right=0, bottom=1080)
    output = Mock(attached_to_desktop=True)
    output.desc.DesktopCoordinates = bounds
    module.__factory = SimpleNamespace(outputs=[[output]])
    monkeypatch.setitem(sys.modules, "bettercam", module)
    backend = capture.BetterCamBackend()
    rect = ClientRect(left=-1800, top=20, right=-100, bottom=900)
    backend.grab(rect)
    module.create.assert_called_once_with(
        device_idx=0, output_idx=0, output_color="RGB", max_buffer_len=1
    )
    module.create.return_value.grab.assert_called_once_with(region=(120, 20, 1820, 900))
    backend.close()
    assert module.create.return_value._duplicator.duplicator is None
    assert module.create.return_value._stagesurf.texture is None
    module.create.return_value.release.assert_called_once()


def test_worker_keeps_frame_when_desktop_does_not_change(worker_setup):
    worker, _manager, backend = worker_setup
    worker.select((42, 123))
    wait_for(lambda: worker.latest().frame is not None)
    backend.grab.return_value = None
    time.sleep(0.1)
    frame = worker.latest().frame
    time.sleep(0.1)
    assert worker.latest().frame is frame
