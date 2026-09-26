import argparse
import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from l2_agent.dataset import (
    DETECTOR_CLASSES, TAXONOMY_VERSION, DetectorSample, load_detector_sample,
)
from l2_agent.logging_setup import setup_logging

logger = logging.getLogger("l2_agent.dataset_export")


class SplitGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    split: Literal["train", "val"]
    samples: list[str] = Field(min_length=1)


def load_groups(path: Path) -> dict[str, SplitGroup]:
    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(
                    f"Повтор ключа '{key}' в groups.json. Объедините кадры группы "
                    "в один список samples и укажите один split для всей группы"
                )
            result[key] = value
        return result

    raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)
    if not isinstance(raw, dict):
        raise ValueError("Группы должны быть JSON объектом")
    return {key: SplitGroup.model_validate(value) for key, value in raw.items()}


def export_dataset(source: Path, destination: Path, groups: dict[str, SplitGroup]) -> Path:
    """
    Экспортируем явно сгруппированные reviewed кадры без случайного смешивания сцен.
    """
    if destination.exists():
        raise ValueError("Каталог экспорта уже существует; выберите новый")
    records: list[tuple[Path, DetectorSample, str, str]] = []
    seen_ids: set[str] = set()
    hashes: dict[str, str] = {}
    for group, assignment in groups.items():
        if not group.strip():
            raise ValueError("Нужно имя группы сцен")
        for sample_id in assignment.samples:
            if len(sample_id) != 32 or any(c not in "0123456789abcdef" for c in sample_id):
                raise ValueError("Некорректный sample_id")
            if sample_id in seen_ids:
                raise ValueError("Кадр включён в несколько групп")
            seen_ids.add(sample_id)
            path = source / sample_id / "sample.json"
            sample, _ = load_detector_sample(path)
            if sample.sample_id != sample_id or sample.annotation_status != "reviewed":
                raise ValueError("Для экспорта нужны reviewed кадры с совпадающим sample_id")
            if sample.taxonomy_version != TAXONOMY_VERSION:
                raise ValueError(
                    f"Кадр {sample_id}: старая схема классов. Откройте в редакторе, "
                    "уточните классы и подтвердите полноту разметки заново"
                )
            previous_split = hashes.setdefault(sample.image_sha256, assignment.split)
            if previous_split != assignment.split:
                raise ValueError("Одинаковое изображение обнаружено в train и val")
            records.append((path, sample, group, assignment.split))
    if {record[3] for record in records} != {"train", "val"}:
        raise ValueError("Нужны непустые train и val из разных групп сцен")
    if not any(sample.objects for _, sample, _, split in records if split == "train"):
        raise ValueError("В train нет размеченных объектов")

    # Каталог создаётся эксклюзивно; только export.json отмечает завершённый экспорт.
    destination.mkdir(parents=True, exist_ok=False)
    report = []
    for split in ("train", "val"):
        (destination / "images" / split).mkdir(parents=True)
        (destination / "labels" / split).mkdir(parents=True)
    for path, sample, group, split in records:
        current, payload = load_detector_sample(path)
        if current != sample:
            raise ValueError("Исходная разметка изменилась во время экспорта")
        (destination / "images" / split / f"{sample.sample_id}.png").write_bytes(payload)
        rows = []
        for obj in sample.objects:
            box = obj.box
            rows.append(
                f"{DETECTOR_CLASSES.index(obj.kind)} "
                f"{(box.x1 + box.x2) / 2:.10f} {(box.y1 + box.y2) / 2:.10f} "
                f"{box.x2 - box.x1:.10f} {box.y2 - box.y1:.10f}\n"
            )
        (destination / "labels" / split / f"{sample.sample_id}.txt").write_text(
            "".join(rows), encoding="utf-8"
        )
        report.append({"group": group, "split": split, "sample": sample.model_dump(mode="json")})
    yaml = (
        f"path: {json.dumps(destination.resolve().as_posix(), ensure_ascii=False)}\n"
        "train: images/train\nval: images/val\nnames:\n"
        + "".join(f"  {index}: {name}\n" for index, name in enumerate(DETECTOR_CLASSES))
    )
    (destination / "dataset.yaml").write_text(yaml, encoding="utf-8")
    (destination / "export.json").write_text(
        json.dumps({"version": 1, "taxonomy_version": TAXONOMY_VERSION,
                    "classes": DETECTOR_CLASSES, "records": report},
                   ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Датасет экспортирован: %s, кадров=%s", destination, len(records))
    return destination / "dataset.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(description="Экспорт reviewed кадров в YOLO detection dataset")
    parser.add_argument("--source", type=Path, default=Path("datasets/detector/raw"))
    parser.add_argument("--groups", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    setup_logging()
    try:
        groups = load_groups(args.groups)
        export_dataset(args.source, args.output, groups)
    except (OSError, ValueError):
        logger.exception("Экспорт не выполнен; незавершённый каталог нельзя использовать для обучения")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
