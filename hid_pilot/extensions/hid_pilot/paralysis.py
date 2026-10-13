"""
Поиск подтверждённой иконки паралича справа от строк пати без OCR.
"""

import json
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.party import PartyProfile


class ParalysisProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    width: int = Field(ge=20)
    height: int = Field(ge=20)
    template: str
    search_width: int = Field(default=180, ge=20, le=400)
    y_padding: int = Field(default=8, ge=0, le=30)
    threshold: float = Field(default=.92, ge=.85, le=1)
    icon_widths: tuple[int, ...] = Field(default=tuple(range(18, 33)), min_length=1, max_length=32)


class ParalysisDetector:
    def __init__(self, path: Path | ParalysisProfile, party: PartyProfile) -> None:
        self.profile = path if isinstance(path, ParalysisProfile) else ParalysisProfile.model_validate_json(path.read_text("utf-8"))
        # Размер исходного снимка не задаёт разрешение игрового окна и размер иконки.
        self.profile = self.profile.model_copy(update={"width": party.width, "height": party.height})
        template_path = Path(self.profile.template) if isinstance(path, ParalysisProfile) else path.parent / self.profile.template
        if template_path.stat().st_size > 8_000_000:
            raise ValueError("Шаблон паралича слишком большой")
        self.template = cv2.imdecode(np.fromfile(template_path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if (self.template is None or not 8 <= self.template.shape[0] <= 1024
                or not 8 <= self.template.shape[1] <= 1024 or np.std(self.template) < 10):
            raise ValueError("Нужна отдельная референсная иконка паралича размером 8–1024 px")
        if not self.profile.icon_widths or any(not 8 <= size <= 64 for size in self.profile.icon_widths):
            raise ValueError("Размеры поиска иконки должны быть в диапазоне 8–64 px")
        height, width = self.template.shape[:2]
        self.templates = [cv2.resize(self.template, (size, max(8, round(height * size / width))),
                                     interpolation=cv2.INTER_AREA) for size in self.profile.icon_widths]
        # Тёмные углы образца заменяются игровым фоном и не должны мешать совпадению.
        self.masks = [(np.max(template, axis=2) > 30).astype(np.uint8) * 255
                      for template in self.templates]
        self.gray_templates = [cv2.cvtColor(template, cv2.COLOR_BGR2GRAY) for template in self.templates]
        self.reference_pixels = [template[mask != 0].astype(np.float32)
                                 for template, mask in zip(self.templates, self.masks, strict=True)]
        self.party = party

    def read(self, image: np.ndarray, slots: tuple[int, ...]) -> tuple[int, ...]:
        cfg = self.profile
        if image.shape[:2] != (cfg.height, cfg.width):
            raise RuntimeError("Размер окна изменён; настройте паралич заново")
        found = []
        for slot in slots:
            boxes = [self.party.box(kind, slot - 1) for kind in ("name", "cp", "hp", "mp")]
            left = round(max(box.x2 for box in boxes) * cfg.width)
            right = min(cfg.width, left + cfg.search_width)
            top = max(0, round(min(box.y1 for box in boxes) * cfg.height) - cfg.y_padding)
            bottom = min(cfg.height, round(max(box.y2 for box in boxes) * cfg.height) + cfg.y_padding)
            if slot < 8:
                next_top = round(self.party.box("name", slot).y1 * cfg.height) - cfg.y_padding
                bottom = min(bottom, next_top)
            crop = image[top:bottom, left:right]
            if crop.size == 0 or np.count_nonzero(np.max(crop, axis=2) > 50) < 12:
                continue
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            scores = []
            compatible = False
            for template, mask, gray_template, reference in zip(
                    self.templates, self.masks, self.gray_templates, self.reference_pixels, strict=True):
                if crop.shape[0] < template.shape[0] or crop.shape[1] < template.shape[1]:
                    continue
                compatible = True
                # Быстрая корреляция формы; дорогую цветовую маску проверяем только у кандидатов.
                matches = cv2.matchTemplate(gray, gray_template, cv2.TM_CCOEFF_NORMED)
                for _ in range(3):
                    _minimum, maximum, _min_location, (x, y) = cv2.minMaxLoc(matches)
                    if maximum < .55:
                        break
                    height, width = template.shape[:2]
                    actual = crop[y:y + height, x:x + width][mask != 0].astype(np.float32)
                    norm = float(np.sqrt(np.sum(reference * reference) * np.sum(actual * actual)))
                    error = float(np.sum((actual - reference) ** 2)) / norm if norm > 0 else 1.
                    scores.append(error)
                    matches[max(0, y - height // 2):y + height // 2 + 1,
                            max(0, x - width // 2):x + width // 2 + 1] = -1
            if not compatible:
                raise ValueError("Область дебаффов слишком мала для шаблона")
            if scores and 1. - min(scores) >= cfg.threshold:
                found.append(slot)
        return tuple(found)


def save_template(image: np.ndarray, icon: BarBox, party: PartyProfile, destination: Path) -> None:
    crop = BarProfile(width=party.width, height=party.height).crop(image, icon)
    if not 8 <= crop.shape[0] <= 64 or not 8 <= crop.shape[1] <= 64 or np.std(crop) < 10:
        raise ValueError("Выделите только иконку паралича, без полос и большого фона")
    template_name = destination.stem + ".png"
    success, encoded = cv2.imencode(".png", crop)
    if not success:
        raise ValueError("Не удалось сохранить шаблон паралича")
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded.tofile(destination.parent / template_name)
    profile = ParalysisProfile(width=party.width, height=party.height, template=template_name)
    destination.write_text(json.dumps(profile.model_dump(), ensure_ascii=False, indent=2), "utf-8")
