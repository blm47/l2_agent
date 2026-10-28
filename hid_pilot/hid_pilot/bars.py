"""
Оценка заполнения подтверждённых полос по свежему BGR-кадру.
"""

from typing import TYPE_CHECKING, Annotated, Literal

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from hid_pilot.numbers import NumbersReader


class BarBox(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)
    x2: float = Field(ge=0, le=1)
    y2: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def ordered(self) -> "BarBox":
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("Пустая область полосы")
        return self

    def expanded_horizontal(self, left: float, right: float) -> "BarBox":
        """
        Добавляет запас от исходной ширины, не выходя за границы кадра.
        """
        if not 0 <= left < float("inf") or not 0 <= right < float("inf"):
            raise ValueError("Запас области должен быть конечным и неотрицательным")
        width = self.x2 - self.x1
        return BarBox(x1=max(0., self.x1 - width * left), y1=self.y1,
                      x2=min(1., self.x2 + width * right), y2=self.y2)


class SummonProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    hp: BarBox
    mp: BarBox
    icon: BarBox
    icon_pixels: list[list[int]]

    @model_validator(mode="after")
    def valid_icon(self) -> "SummonProfile":
        if (len(self.icon_pixels) != 32 or any(len(row) != 32 for row in self.icon_pixels)
                or any(not 0 <= pixel <= 255 for row in self.icon_pixels for pixel in row)
                or float(np.asarray(self.icon_pixels).std()) < 10):
            raise ValueError("Нужен контрастный эталон иконки самона 32×32")
        return self


class BarProfile(BaseModel):
    summon: SummonProfile | None = None
    system_chat: BarBox | None = None
    system_chat_enabled: bool = False
    model_config = ConfigDict(extra="forbid")
    width: int = Field(ge=20)
    height: int = Field(ge=20)
    bars: dict[str, BarBox] = Field(default_factory=dict)
    numbers: dict[str, BarBox] = Field(default_factory=dict)
    target_player_panel: BarBox | None = None
    own_level: BarBox | None = None
    target_level: BarBox | None = None
    target_panel: BarBox | None = None
    target_hp_presence_box: BarBox | None = None
    target_kind_names: dict[str, Literal["monster", "npc"]] = Field(default_factory=dict)
    target_dead_marker: BarBox | None = None
    target_dead_mask: list[Annotated[str, Field(min_length=48, max_length=48, pattern="^[01]+$")]] = Field(
        default_factory=list, max_length=48)

    def calibrate_dead_marker(self, image: np.ndarray, box: BarBox) -> None:
        crop = self.crop(image, box)
        mask = red_marker_mask(crop)
        ratio = float(np.mean(mask))
        if not .1 <= ratio <= .9:
            raise ValueError("Выделите красный череп внутри портрета, без кольца и текста")
        self.target_dead_marker = box
        self.target_dead_mask = ["".join("1" if pixel else "0" for pixel in row) for row in mask]

    def read_level(self, image: np.ndarray, name: Literal["own_level", "target_level"],
                   reader: "NumbersReader | None" = None) -> int | None:
        """
        Читает только явно размеченный уровень, не выводя его из HP или имени.
        """
        from hid_pilot.numbers import NumbersReader, shared_numbers

        box = getattr(self, name)
        if box is None or image.shape[:2] != (self.height, self.width):
            return None
        engine = reader or shared_numbers()
        crop = self.crop(image, box)
        if isinstance(engine, NumbersReader):
            return engine.read_level(crop)
        rows = engine.detect(crop)
        values = [int(text.strip()) for _, text, score in rows
                  if score >= .9 and text.strip().isascii() and text.strip().isdigit()
                  and 1 <= int(text.strip()) <= 150]
        return values[0] if len(values) == 1 else None

    def dead_marker_visible(self, image: np.ndarray) -> bool:
        if self.target_dead_marker is None or len(self.target_dead_mask) != 48:
            return False
        rows = self.target_dead_mask
        if any(len(row) != 48 or set(row) - {"0", "1"} for row in rows):
            return False
        expected = np.array([[char == "1" for char in row] for row in rows])
        crop = self.crop(image, self.target_dead_marker)
        if min(crop.shape[:2]) < 3:
            return False
        current = red_marker_mask(crop)
        union = np.count_nonzero(current | expected)
        return bool(union > 0 and np.count_nonzero(current & expected) / union >= .9)
    target_zero_box: BarBox | None = None
    target_zero_mask: list[str] = Field(default_factory=list, max_length=100)

    def calibrate_zero(self, image: np.ndarray, box: BarBox) -> None:
        mask = text_mask(self.crop(image, box))
        if mask.size > 20000 or np.count_nonzero(mask) < 15:
            raise ValueError("Выделите компактную надпись 0 / максимум HP, без большой области фона")
        self.target_zero_box = box
        self.target_zero_mask = ["".join("1" if pixel else "0" for pixel in row) for row in mask]

    def crop(self, image: np.ndarray, box: BarBox) -> np.ndarray:
        height, width = image.shape[:2]
        if (width, height) != (self.width, self.height):
            raise RuntimeError("Размер окна изменён; настройте полосы заново")
        return image[round(box.y1 * height):round(box.y2 * height),
                     round(box.x1 * width):round(box.x2 * width)]

    def zero_visible(self, image: np.ndarray) -> bool:
        if self.target_zero_box is None or not self.target_zero_mask:
            return False
        current = text_mask(self.crop(image, self.target_zero_box))
        rows = self.target_zero_mask
        if not rows or any(len(row) != len(rows[0]) or set(row) - {"0", "1"} for row in rows):
            return False
        expected = np.array([[pixel == "1" for pixel in row] for row in rows])
        # Сверяем надпись целиком: отсутствие панели и другой числитель не дают ноль.
        return current.shape == expected.shape and np.array_equal(current, expected)

    def read(self, image: np.ndarray, name: str) -> float | None:
        return self.read_detail(image, name)[0]

    def read_detail(self, image: np.ndarray, name: str) -> tuple[float | None, str]:
        height, width = image.shape[:2]
        if (width, height) != (self.width, self.height):
            raise RuntimeError("Размер окна изменён; настройте полосы заново")
        if name in self.numbers:
            from hid_pilot.numbers import shared_numbers
            result = shared_numbers().read(self.crop(image, self.numbers[name]))
            if result is not None:
                return result.fraction, f"OCR {result.current}/{result.maximum}"
        if name not in self.bars:
            if name in self.numbers:
                return None, "OCR: неизвестно"
            raise RuntimeError(f"Полоса {name} не настроена")
        box = self.bars[name]
        crop = image[round(box.y1 * height):round(box.y2 * height),
                     round(box.x1 * width):round(box.x2 * width)]
        value = bar_fraction(crop, name)
        if value is None and name == "target_hp" and self.zero_visible(image):
            return 0.0, "шаблон нулевого HP"
        source = "полоса (OCR не распознан)" if name in self.numbers else "полоса"
        return value, source


def text_mask(bgr: np.ndarray) -> np.ndarray:
    low = np.min(bgr, axis=2)
    high = np.max(bgr, axis=2)
    return (low >= 125) & (high.astype(int) - low.astype(int) <= 65)


def red_marker_mask(bgr: np.ndarray) -> np.ndarray:
    if min(bgr.shape[:2]) < 3:
        raise ValueError("Область маркера смерти слишком мала")
    sample = cv2.resize(bgr, (48, 48), interpolation=cv2.INTER_AREA)
    blue, green, red = sample.astype(int).transpose(2, 0, 1)
    return (red >= 45) & (red > green * 1.6) & (red > blue * 1.6)


def bar_fraction(bgr: np.ndarray, name: str) -> float | None:
    height, width = bgr.shape[:2]
    if height < 3 or width < 20 or width / height < 4:
        return None
    hue, saturation, value = cv2.split(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV))
    if name in {"hp", "target_hp"}:
        color = (hue <= 12) | (hue >= 165)
    elif name == "mp":
        color = (hue >= 90) & (hue <= 135)
    elif name == "cp":
        color = (hue >= 15) & (hue <= 40)
    else:
        raise ValueError("Неизвестная полоса")
    mask = color & (saturation >= 85) & (value >= 45)
    indices = np.flatnonzero(np.mean(mask, axis=0) >= .35)
    if len(indices) < 3:
        return None
    start, end = int(indices[0]), int(indices[-1]) + 1
    if start > max(2, int(width * .05)):
        return None
    if np.max(np.diff(indices) - 1, initial=0) > max(3, int(width * .04)):
        return None
    if float(np.mean(mask[:, start:end])) < .45:
        return None
    return end / width
