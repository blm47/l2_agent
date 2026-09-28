import hashlib
import time

import cv2
import numpy as np
import pytest

from l2_agent.capture import CapturedFrame
from l2_agent.dataset import DetectorSample, save_detector_sample
from l2_agent.dataset import ObjectAnnotation, load_detector_sample, save_annotations
from l2_agent.dataset import DETECTOR_CLASSES
from l2_agent.roi import NormalizedBox
from l2_agent.geometry import ClientRect
from l2_agent.windows import WindowSnapshot
from l2_agent.world_state import WorldState


@pytest.fixture
def source():
    rect = ClientRect(left=0, top=0, right=40, bottom=30)
    rgb = np.zeros((30, 40, 3), dtype=np.uint8)
    rgb[5:10] = (210, 35, 60)
    frame = CapturedFrame(sequence=7, timestamp=time.monotonic(), rect=rect, rgb=rgb)
    window = WindowSnapshot(hwnd=42, pid=123, title="LU4", minimized=False,
                            focused=True, rect=rect)
    return frame, window


def test_roundtrip_original_pixels_and_metadata(tmp_path, source):
    frame, window = source
    state = WorldState(frame_id=frame.sequence, timestamp=frame.timestamp)
    path = save_detector_sample(frame, window, root=tmp_path, world_state=state)
    sample = DetectorSample.model_validate_json((path / "sample.json").read_text("utf-8"))
    payload = (path / "image.png").read_bytes()
    decoded = cv2.cvtColor(cv2.imdecode(np.frombuffer(payload, np.uint8), 1), cv2.COLOR_BGR2RGB)
    np.testing.assert_array_equal(decoded, frame.rgb)
    assert sample.image_sha256 == hashlib.sha256(payload).hexdigest()
    assert sample.world_state == state
    assert sample.annotation_status == "unlabeled"
    assert sample.objects == []
    reviewed = sample.model_dump() | {"annotation_status": "reviewed"}
    assert DetectorSample.model_validate(reviewed).objects == []
    with pytest.raises(ValueError):
        DetectorSample.model_validate(sample.model_dump() | {"objects": [
            {"kind": "mob", "box": {"x1": 0, "y1": 0, "x2": 1, "y2": 1}}
        ]})


@pytest.mark.parametrize("change", ["stale", "future", "focus", "geometry", "state"])
def test_rejects_invalid_capture_without_files(tmp_path, source, change):
    frame, window = source
    kwargs = {}
    if change in {"stale", "future"}:
        frame = frame.model_copy(update={"timestamp": time.monotonic() + (10 if change == "future" else -10)})
    elif change == "focus":
        window = window.model_copy(update={"focused": False})
    elif change == "geometry":
        window = window.model_copy(update={"rect": None})
    else:
        kwargs["world_state"] = WorldState(frame_id=99, timestamp=frame.timestamp)
    with pytest.raises(ValueError):
        save_detector_sample(frame, window, root=tmp_path, **kwargs)
    assert list(tmp_path.iterdir()) == []


def test_failed_write_leaves_no_partial_sample(tmp_path, source, monkeypatch):
    from pathlib import Path

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", fail)
    with pytest.raises(OSError, match="disk full"):
        save_detector_sample(*source, root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_annotations_roundtrip_and_stale_editor(tmp_path, source):
    path = save_detector_sample(*source, root=tmp_path) / "sample.json"
    original, payload = load_detector_sample(path)
    objects = [ObjectAnnotation(kind="mob", box=NormalizedBox(x1=.1, y1=.2, x2=.4, y2=.6))]
    save_annotations(path, original, objects)
    updated, same_payload = load_detector_sample(path)
    assert updated.objects == objects
    assert updated.annotation_status == "reviewed"
    assert payload == same_payload
    with pytest.raises(ValueError, match="редактором"):
        save_annotations(path, original, [])
    assert load_detector_sample(path)[0] == updated


def test_changed_image_rejected(tmp_path, source):
    path = save_detector_sample(*source, root=tmp_path) / "sample.json"
    (path.parent / "image.png").write_bytes(b"broken")
    with pytest.raises(ValueError, match="SHA-256"):
        load_detector_sample(path)


def test_editor_review_remove_cancel_and_save(tmp_path, source):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog
    from l2_agent.dataset_editor import DatasetEditor

    app = QApplication.instance() or QApplication([])
    path = save_detector_sample(*source, root=tmp_path) / "sample.json"
    original = path.read_bytes()
    editor = DatasetEditor(path)
    editor.show()
    app.processEvents()
    box = NormalizedBox(x1=.1, y1=.2, x2=.4, y2=.6)
    editor.add_object(box)
    editor.save()
    assert path.read_bytes() == original
    editor.objects.setCurrentRow(0)
    editor.remove_object()
    assert editor.canvas.objects == []
    editor.add_object(box)
    editor.reject()
    assert path.read_bytes() == original
    editor.close()
    editor = DatasetEditor(path)
    editor.reviewed.setChecked(True)
    editor.save()
    assert editor.result() == QDialog.DialogCode.Accepted
    sample, _ = load_detector_sample(path)
    assert sample.annotation_status == "reviewed"
    assert sample.objects == []
    editor.close()


@pytest.mark.parametrize("kind", DETECTOR_CLASSES)
def test_all_classes_can_be_saved(tmp_path, source, kind):
    path = save_detector_sample(*source, root=tmp_path) / "sample.json"
    original, _ = load_detector_sample(path)
    obj = ObjectAnnotation(kind=kind, box=NormalizedBox(x1=.1, y1=.2, x2=.4, y2=.6))
    save_annotations(path, original, [obj])
    updated, _ = load_detector_sample(path)
    assert updated.taxonomy_version == 2
    assert updated.objects == [obj]


def test_old_sample_only_upgraded_on_explicit_save(tmp_path, source):
    import json

    path = save_detector_sample(*source, root=tmp_path) / "sample.json"
    data = json.loads(path.read_text("utf-8"))
    data.pop("taxonomy_version")
    path.write_text(json.dumps(data), encoding="utf-8")
    original_bytes = path.read_bytes()
    sample, _ = load_detector_sample(path)
    assert sample.taxonomy_version == 1
    assert path.read_bytes() == original_bytes
    save_annotations(path, sample, [])
    assert load_detector_sample(path)[0].taxonomy_version == 2


def test_reclassify_keeps_box_and_requires_review(tmp_path, source):
    from PySide6.QtWidgets import QApplication
    from l2_agent.dataset_editor import DatasetEditor

    app = QApplication.instance() or QApplication([])
    path = save_detector_sample(*source, root=tmp_path) / "sample.json"
    editor = DatasetEditor(path)
    box = NormalizedBox(x1=.1, y1=.2, x2=.4, y2=.6)
    editor.add_object(box)
    editor.objects.setCurrentRow(0)
    editor.reviewed.setChecked(True)
    editor.kind.setCurrentText("mob_selected")
    editor.selected.setCurrentIndex(2)
    editor.reclassify_object()
    assert editor.canvas.objects[0].box == box
    assert editor.canvas.objects[0].kind == "mob_selected"
    assert editor.canvas.objects[0].selected is True
    assert not editor.reviewed.isChecked()
    editor.reviewed.setChecked(True)
    editor.save()
    assert load_detector_sample(path)[0].objects[0].kind == "mob_selected"
    editor.close()


def test_save_next_requires_review_and_preserves_next_on_cancel(tmp_path, source):
    from PySide6.QtWidgets import QApplication
    from l2_agent.dataset_editor import DatasetEditor

    app = QApplication.instance() or QApplication([])
    paths = sorted(save_detector_sample(*source, root=tmp_path) / "sample.json"
                   for _ in range(2))
    editor = DatasetEditor(paths[0])
    next_bytes = paths[1].read_bytes()
    editor.add_object(NormalizedBox(x1=.1, y1=.2, x2=.4, y2=.6))
    editor.objects.setCurrentRow(0)
    assert editor.canvas.active_index == 0
    editor.save_next()
    assert editor.path == paths[0]
    assert load_detector_sample(paths[0])[0].annotation_status == "unlabeled"
    editor.reviewed.setChecked(True)
    editor.save_next()
    assert editor.path == paths[1]
    assert not editor.reviewed.isChecked()
    assert not editor.next_button.isEnabled()
    assert len(load_detector_sample(paths[0])[0].objects) == 1
    editor.reject()
    assert paths[1].read_bytes() == next_bytes
    editor.close()


@pytest.mark.parametrize("empty", [False, True])
def test_grabber_preannotations_open_in_editor(tmp_path, source, empty):
    import importlib.util
    import os
    from pathlib import Path
    from unittest.mock import Mock
    from uuid import UUID

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from l2_agent.dataset_editor import DatasetEditor
    from l2_agent.world_state import DetectedObject

    spec = importlib.util.spec_from_file_location(
        "grab_dataset", Path(__file__).resolve().parents[1] / "scripts/grab_pic_dataset.py"
    )
    grabber = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(grabber)
    detected = DetectedObject(kind="mob_selected", confidence=.9,
                              box=NormalizedBox(x1=.1, y1=.2, x2=.4, y2=.6))
    detector = Mock()
    detector.detect.return_value = () if empty else (detected,)
    directory = grabber.save_preannotated_sample(*source, detector, tmp_path)
    assert UUID(directory.name).hex == directory.name
    assert {p.name for p in directory.iterdir()} == {"image.png", "sample.json"}
    sample, payload = load_detector_sample(directory / "sample.json")
    assert sample.annotation_status == "preannotated"
    assert len(sample.objects) == (0 if empty else 1)
    if not empty:
        assert sample.objects[0].box == detected.box
        assert sample.objects[0].selected is True
    rgb = cv2.cvtColor(cv2.imdecode(np.frombuffer(payload, np.uint8), 1), cv2.COLOR_BGR2RGB)
    np.testing.assert_array_equal(rgb, source[0].rgb)
    app = QApplication.instance() or QApplication([])
    editor = DatasetEditor(directory / "sample.json")
    try:
        assert editor.canvas.objects == sample.objects
        assert not editor.reviewed.isChecked()
        assert not editor.persist()
        editor.reviewed.setChecked(True)
        assert editor.persist()
        assert load_detector_sample(directory / "sample.json")[0].annotation_status == "reviewed"
    finally:
        editor.close()
    detector.detect.side_effect = RuntimeError("inference failed")
    with pytest.raises(RuntimeError):
        grabber.save_preannotated_sample(*source, detector, tmp_path)
    samples = [load_detector_sample(p)[0] for p in tmp_path.glob("*/sample.json")]
    assert sorted(s.annotation_status for s in samples) == ["reviewed", "unlabeled"]
