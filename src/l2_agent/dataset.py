import hashlib
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, get_args
from uuid import uuid4

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from l2_agent.capture import CapturedFrame
from l2_agent.roi import NormalizedBox, RoiProfile
from l2_agent.windows import WindowSnapshot
from l2_agent.world_state import WorldState

logger = logging.getLogger("l2_agent.dataset")
DEFAULT_DATASET_PATH = Path("datasets/detector/raw")
TAXONOMY_VERSION = 2
DetectorClass = Literal[
    "mob", "npc", "player", "corpse", "loot", "ui_window",
    "mob_selected", "player_other", "minimap", "hotbar", "chat", "system_chat",
    "skills_panel", "tool_bar", "character_status_panel", "inventory",
    "actions_panel", "quests_panel", "clan_panel", "general_menu_panel",
    "current_exp_panel", "current_adena_panel", "current_time_panel",
]
# Порядок исходных шести классов сохраняем; новые ID только добавляем в конец.
DETECTOR_CLASSES: tuple[str, ...] = get_args(DetectorClass)
LEGACY_CLASSES = DETECTOR_CLASSES[:6]


def load_detector_sample(path: Path) -> tuple["DetectorSample", bytes]:
    sample = DetectorSample.model_validate_json(path.read_text(encoding="utf-8"))
    payload = (path.parent / sample.image_file).read_bytes()
    if hashlib.sha256(payload).hexdigest() != sample.image_sha256:
        raise ValueError("Изображение изменилось: SHA-256 не совпадает")
    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[:2] != (sample.height, sample.width):
        raise ValueError("Размер изображения не соответствует метаданным")
    return sample, payload


def save_annotations(
    path: Path, original: "DetectorSample", objects: list["ObjectAnnotation"]
) -> None:
    current, _ = load_detector_sample(path)
    if current != original:
        raise ValueError("Разметка изменена другим редактором. Откройте кадр заново")
    updated = DetectorSample.model_validate(
        original.model_dump() | {
            "annotation_status": "reviewed", "objects": objects,
            "taxonomy_version": TAXONOMY_VERSION,
        }
    )
    temporary = path.with_name(f".{uuid4().hex}.tmp")
    try:
        temporary.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    logger.info("Разметка сохранена: %s, объектов=%s", path, len(objects))


class ObjectAnnotation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: DetectorClass
    box: NormalizedBox
    selected: bool | None = None

    @model_validator(mode="after")
    def validate_selection(self) -> "ObjectAnnotation":
        if self.kind == "mob_selected" and self.selected is False:
            raise ValueError("mob_selected не может иметь selected=false")
        return self


class DetectorSample(BaseModel):
    """
    Ручная разметка отделена от гипотез WorldState; пустой список ещё не negative sample.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    version: Literal[1] = 1
    # Отсутствие поля в старых файлах означает исходную схему из шести классов.
    taxonomy_version: Literal[1, 2] = 1
    sample_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    image_file: Literal["image.png"] = "image.png"
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    saved_at_utc: datetime
    frame_id: int = Field(ge=0)
    frame_timestamp: float = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    window: WindowSnapshot
    world_state: WorldState | None = None
    roi_profile: RoiProfile | None = None
    annotation_status: Literal["unlabeled", "reviewed"] = "unlabeled"
    objects: list[ObjectAnnotation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_sample(self) -> "DetectorSample":
        if self.taxonomy_version == 1 and any(
            obj.kind not in LEGACY_CLASSES for obj in self.objects
        ):
            raise ValueError("Новые классы требуют taxonomy_version=2")
        if self.annotation_status == "unlabeled" and self.objects:
            raise ValueError("Объекты требуют явного статуса reviewed")
        if self.saved_at_utc.utcoffset() is None:
            raise ValueError("Время сохранения должно содержать timezone")
        if self.window.rect is None or (self.width, self.height) != (
            self.window.rect.width, self.window.rect.height
        ):
            raise ValueError("Размер кадра не совпадает с окном")
        if self.world_state is not None and (
            self.world_state.frame_id != self.frame_id
            or self.world_state.timestamp != self.frame_timestamp
        ):
            raise ValueError("WorldState относится к другому кадру")
        if self.roi_profile is not None and not self.roi_profile.matches(self.width, self.height):
            raise ValueError("ROI не соответствует размеру кадра")
        return self


def save_detector_sample(
    frame: CapturedFrame,
    window: WindowSnapshot,
    *,
    root: Path = DEFAULT_DATASET_PATH,
    world_state: WorldState | None = None,
    roi_profile: RoiProfile | None = None,
) -> Path:
    """
    Сохраняем один свежий исходный кадр без preview overlays и не генерируем метки.
    """
    if not 0 <= time.monotonic() - frame.timestamp <= 1:
        raise ValueError("Для датасета нужен свежий кадр")
    if window.minimized or not window.focused or window.rect != frame.rect:
        raise ValueError("Выбранная игра должна быть в фокусе с прежней геометрией")
    rgb = frame.rgb
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("Ожидается RGB uint8 кадр")
    success, encoded = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not success:
        raise OSError("Не удалось закодировать PNG")
    payload = encoded.tobytes()
    sample_id = uuid4().hex
    sample = DetectorSample(
        taxonomy_version=TAXONOMY_VERSION,
        sample_id=sample_id, image_sha256=hashlib.sha256(payload).hexdigest(),
        saved_at_utc=datetime.now(timezone.utc), frame_id=frame.sequence,
        frame_timestamp=frame.timestamp, width=rgb.shape[1], height=rgb.shape[0],
        window=window, world_state=world_state, roi_profile=roi_profile,
    )
    root.mkdir(parents=True, exist_ok=True)
    staging = root / ("." + sample_id + ".tmp")
    destination = root / sample_id
    if destination.exists():
        raise FileExistsError(destination)
    staging.mkdir()
    try:
        (staging / "image.png").write_bytes(payload)
        (staging / "sample.json").write_text(sample.model_dump_json(indent=2), encoding="utf-8")
        staging.rename(destination)
    except Exception:
        # Убираем только созданные здесь файлы, не трогаем другие примеры датасета.
        for name in ("image.png", "sample.json"):
            (staging / name).unlink(missing_ok=True)
        staging.rmdir()
        raise
    logger.info("Кадр для разметки сохранён: %s, frame=%s", destination, frame.sequence)
    return destination
