import json
import time

import numpy as np
import pytest

from l2_agent.capture import CapturedFrame
from l2_agent.dataset import ObjectAnnotation, load_detector_sample, save_annotations, save_detector_sample
from l2_agent.dataset_export import SplitGroup, export_dataset
from l2_agent.dataset_export import load_groups
from l2_agent.dataset import DETECTOR_CLASSES


@pytest.mark.parametrize("text", [
    '{"scene": {"split": "train", "samples": ["a"]}, "scene": {"split": "val", "samples": ["b"]}}',
    '{"scene": {"split": "train", "split": "val", "samples": ["a"]}}',
])
def test_duplicate_group_keys_rejected(tmp_path, text):
    path = tmp_path / "groups.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="Повтор ключа"):
        load_groups(path)


def test_group_load_preserves_all_samples(tmp_path):
    path = tmp_path / "groups.json"
    path.write_text('{"scene": {"split": "train", "samples": ["a", "b", "c"]}}', encoding="utf-8")
    assert load_groups(path)["scene"].samples == ["a", "b", "c"]
from l2_agent.geometry import ClientRect
from l2_agent.roi import NormalizedBox
from l2_agent.windows import WindowSnapshot


@pytest.fixture
def dataset(tmp_path):
    root = tmp_path / "raw"
    rect = ClientRect(left=0, top=0, right=40, bottom=30)
    window = WindowSnapshot(hwnd=1, pid=2, title="LU4", focused=True, minimized=False, rect=rect)
    ids = []
    for index in range(2):
        frame = CapturedFrame(sequence=index, timestamp=time.monotonic(), rect=rect,
                              rgb=np.full((30, 40, 3), index * 100, dtype=np.uint8))
        path = save_detector_sample(frame, window, root=root) / "sample.json"
        sample, _ = load_detector_sample(path)
        objects = [ObjectAnnotation(kind="mob", box=NormalizedBox(x1=.1, y1=.2, x2=.5, y2=.8))]
        save_annotations(path, sample, objects if index == 0 else [])
        ids.append(sample.sample_id)
    groups = {"scene-a": SplitGroup(split="train", samples=[ids[0]]),
              "scene-b": SplitGroup(split="val", samples=[ids[1]])}
    return root, groups, ids


def test_export_coordinates_classes_and_negative_sample(tmp_path, dataset):
    root, groups, ids = dataset
    output = tmp_path / "export"
    yaml = export_dataset(root, output, groups)
    assert "0: mob" in yaml.read_text("utf-8")
    values = (output / "labels/train" / f"{ids[0]}.txt").read_text().split()
    assert list(map(float, values)) == pytest.approx([0, .3, .5, .4, .6])
    assert (output / "labels/val" / f"{ids[1]}.txt").read_bytes() == b""
    assert len(json.loads((output / "export.json").read_text())['records']) == 2
    with pytest.raises(ValueError, match="существует"):
        export_dataset(root, output, groups)


@pytest.mark.parametrize("problem", ["unreviewed", "duplicate", "missing_val", "leak", "corrupt"])
def test_rejects_invalid_dataset_before_creating_output(tmp_path, dataset, problem):
    root, groups, ids = dataset
    path = root / ids[1] / "sample.json"
    data = json.loads(path.read_text("utf-8"))
    if problem == "unreviewed":
        data["annotation_status"] = "unlabeled"
    elif problem == "duplicate":
        groups["scene-b"].samples = [ids[0]]
    elif problem == "missing_val":
        groups.pop("scene-b")
    elif problem == "leak":
        first, payload = load_detector_sample(root / ids[0] / "sample.json")
        (path.parent / "image.png").write_bytes(payload)
        data["image_sha256"] = first.image_sha256
    else:
        (path.parent / "image.png").write_bytes(b"broken")
    path.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "export"
    with pytest.raises(ValueError):
        export_dataset(root, output, groups)
    assert not output.exists()


def test_export_new_class_ids_and_taxonomy(tmp_path, dataset):
    root, groups, ids = dataset
    path = root / ids[0] / "sample.json"
    original, _ = load_detector_sample(path)
    objects = [ObjectAnnotation(kind=kind, box=NormalizedBox(x1=.1, y1=.2, x2=.5, y2=.8))
               for kind in DETECTOR_CLASSES]
    save_annotations(path, original, objects)
    output = tmp_path / "expanded"
    export_dataset(root, output, groups)
    report = json.loads((output / "export.json").read_text("utf-8"))
    assert report["taxonomy_version"] == 2
    assert report["classes"][:6] == ["mob", "npc", "player", "corpse", "loot", "ui_window"]
    rows = (output / "labels/train" / f"{ids[0]}.txt").read_text().splitlines()
    assert [int(row.split()[0]) for row in rows] == list(range(23))


def test_export_requires_review_for_expanded_taxonomy(tmp_path, dataset):
    root, groups, ids = dataset
    path = root / ids[0] / "sample.json"
    data = json.loads(path.read_text("utf-8"))
    data.pop("taxonomy_version")
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="старая схема"):
        export_dataset(root, tmp_path / "expanded", groups)
    assert not (tmp_path / "expanded").exists()
