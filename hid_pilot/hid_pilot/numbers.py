"""
Чтение одной числовой строки current / maximum на CPU.
"""

import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Any

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger("hid_pilot.numbers")


class ResourceNumbers(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)
    current: int = Field(ge=0, le=1_000_000_000)
    maximum: int = Field(gt=0, le=1_000_000_000)
    confidence: float = Field(ge=.85, le=1)

    @model_validator(mode="after")
    def valid_range(self) -> "ResourceNumbers":
        if self.current > self.maximum:
            raise ValueError("Текущее значение больше максимума")
        return self

    @property
    def fraction(self) -> float:
        return self.current / self.maximum


def parse_numbers(text: str, confidence: float) -> ResourceNumbers | None:
    match = re.fullmatch(r"\s*(\d[\d ]*)\s*/\s*(\d[\d ]*)\s*", text)
    if match is None:
        return None
    try:
        return ResourceNumbers(current=int(match[1].replace(" ", "")),
                               maximum=int(match[2].replace(" ", "")), confidence=confidence)
    except ValueError:
        return None


class NumbersReader:
    def __init__(self) -> None:
        self.engine: Any = None
        self.lock = threading.RLock()
        self.cache: OrderedDict[tuple, tuple[float, ResourceNumbers]] = OrderedDict()
        self.level_cache: OrderedDict[tuple, int] = OrderedDict()

    def read_level(self, bgr: np.ndarray) -> int | None:
        """
        Повторяет OCR уровня только при изменении видимых символов.
        """
        from hid_pilot.target import target_text_signature

        signature = (bgr.shape, target_text_signature(bgr))
        if signature in self.level_cache:
            self.level_cache.move_to_end(signature)
            return self.level_cache[signature]
        rows = self.detect(bgr)
        values = [int(text.strip()) for _, text, score in rows
                  if score >= .9 and text.strip().isascii() and text.strip().isdigit()
                  and 1 <= int(text.strip()) <= 150]
        value = values[0] if len(values) == 1 else None
        if value is not None:
            self.level_cache[signature] = value
            while len(self.level_cache) > 32:
                self.level_cache.popitem(last=False)
        return value

    def prepare(self) -> None:
        with self.lock:
            if self.engine is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                except ImportError as exc:
                    raise RuntimeError("Для цифр установите HID Pilot с extra ocr") from exc
                self.engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1,
                                       use_cls=False, det_use_cuda=False, rec_use_cuda=False)
                logger.info("Чтение цифр: OCR на CPU загружен")

    def read(self, bgr: np.ndarray) -> ResourceNumbers | None:
        if bgr.size == 0 or bgr.shape[0] < 3 or bgr.shape[1] < 8:
            return None
        self.prepare()
        low, high = np.min(bgr, axis=2), np.max(bgr, axis=2)
        foreground = (low >= 125) & (high.astype(int) - low.astype(int) <= 65)
        if np.count_nonzero(foreground) < 4:
            return None
        mask = cv2.cvtColor(np.where(foreground, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        signature = (foreground.shape, foreground.tobytes())
        variants = [(mask, bgr)]
        occupied = np.flatnonzero(np.any(foreground, axis=1))
        bands = np.split(occupied, np.flatnonzero(np.diff(occupied) > 1) + 1)
        if len(bands) > 1:
            # Соседняя строка мира просвечивает через пустую полосу мёртвой цели.
            # Обрезанные краем строки не читаем: из части цифры нельзя выводить HP.
            complete = [band for band in bands
                        if len(band) >= 3 and band[0] > 0 and band[-1] < bgr.shape[0] - 1]
            if not complete or len(complete) > 3:
                return None
            variants = []
            for band in complete:
                # Светлая маска не включает тёмную обводку цифр: сохраняем её из исходника.
                # Соседняя строка, включая обрезанный текст мира, в эти поля не попадает.
                before = occupied[occupied < band[0]]
                after = occupied[occupied > band[-1]]
                top = max(0, int(band[0]) - 1, int(before[-1]) + 1 if len(before) else 0)
                bottom = min(bgr.shape[0], int(band[-1]) + 2,
                             int(after[0]) if len(after) else bgr.shape[0])
                variants.append((
                    cv2.copyMakeBorder(mask[top:bottom], 2, 2, 0, 0,
                                       cv2.BORDER_CONSTANT, value=(255, 255, 255)),
                    cv2.copyMakeBorder(bgr[top:bottom], 2, 2, 0, 0,
                                       cv2.BORDER_CONSTANT, value=(0, 0, 0)),
                ))
        readings = []
        with self.lock:
            cached = self.cache.get(signature)
            if cached is not None and cached[1].current > 0 and 0 <= time.monotonic() - cached[0] <= 2:
                self.cache.move_to_end(signature)
                return cached[1]
            for row in variants:
                for crop in row:
                    resized = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                    result, _ = self.engine(resized, use_det=False, use_cls=False)
                    if result is not None and len(result) == 1:
                        text, score = result[0]
                        parsed = parse_numbers(str(text), float(score))
                        if parsed is not None:
                            readings.append(parsed)
        if not readings or len({(r.current, r.maximum) for r in readings}) != 1:
            return None
        result = max(readings, key=lambda reading: reading.confidence)
        # Смерть должна подтверждаться новым OCR, а не повтором закешированного нуля.
        if result.current == 0:
            return result
        with self.lock:
            self.cache[signature] = time.monotonic(), result
            self.cache.move_to_end(signature)
            while len(self.cache) > 32:
                self.cache.popitem(last=False)
        return result

    def read_text(self, bgr: np.ndarray) -> tuple[str, float] | None:
        """
        Распознаёт явно размеченную строку имени без поиска текста на кадре.
        """
        if bgr.size == 0 or min(bgr.shape[:2]) < 3:
            return None
        self.prepare()
        low, high = np.min(bgr, axis=2), np.max(bgr, axis=2)
        foreground = (low >= 125) & (high.astype(int) - low.astype(int) <= 65)
        mask = cv2.cvtColor(np.where(foreground, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        candidates = []
        with self.lock:
            # У готовой строки убираем лишний фон и добавляем поля: детектор текста не нужен.
            ys, xs = np.nonzero(foreground)
            if len(xs) >= 4:
                tight = bgr[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
                tight = cv2.copyMakeBorder(tight, 4, 4, 8, 8, cv2.BORDER_CONSTANT, value=(0, 0, 0))
                result, _ = self.engine(cv2.resize(tight, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC),
                                        use_det=False, use_cls=False)
                if result is not None and len(result) == 1:
                    text, score = result[0]
                    text = " ".join(str(text).split())
                    if float(score) >= .9 and any(char.isalpha() for char in text) and "/" not in text:
                        return text, float(score)
            for crop in (bgr, mask):
                result, _ = self.engine(cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC),
                                        use_det=False, use_cls=False)
                if result is not None and len(result) == 1:
                    text, score = result[0]
                    text = " ".join(str(text).split())
                    if float(score) >= .9 and any(char.isalpha() for char in text) and "/" not in text:
                        candidates.append((text, float(score)))
        return max(candidates, key=lambda item: item[1]) if candidates else None

    def detect(self, bgr: np.ndarray) -> list[tuple[list[list[float]], str, float]]:
        self.prepare()
        with self.lock:
            result, _ = self.engine(cv2.resize(bgr, None, fx=2, fy=2),
                                    use_det=True, use_cls=False)
        return [([[float(v) / 2 for v in point] for point in box], str(text), float(score))
                for box, text, score in result or []]


_reader = NumbersReader()


def shared_numbers() -> NumbersReader:
    return _reader
