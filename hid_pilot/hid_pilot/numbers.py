"""
Чтение одной числовой строки current / maximum на CPU.
"""

import logging
import re
import threading
from typing import Any

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger("hid_pilot.numbers")


class ResourceNumbers(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)
    current: int = Field(ge=0, le=1_000_000_000)
    maximum: int = Field(gt=0, le=1_000_000_000)
    confidence: float = Field(ge=.9, le=1)

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
        readings = []
        with self.lock:
            for crop in (mask, bgr):
                resized = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                result, _ = self.engine(resized, use_det=False, use_cls=False)
                if result is not None and len(result) == 1:
                    text, score = result[0]
                    parsed = parse_numbers(str(text), float(score))
                    if parsed is not None:
                        readings.append(parsed)
        if not readings or len({(r.current, r.maximum) for r in readings}) != 1:
            return None
        return max(readings, key=lambda reading: reading.confidence)

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
