import logging
import threading
from typing import Any, Protocol

import cv2
import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

from l2_agent.roi import NormalizedBox

logger = logging.getLogger("l2_agent.ocr")


class TextObservation(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)
    text: str
    score: float = Field(ge=0, le=1)
    box: NormalizedBox


class OCRProvider(Protocol):
    def recognize(self, rgb: NDArray[np.uint8]) -> list[TextObservation]: ...


class RapidOCRProvider:
    """
    Одна ленивая CPU-модель и lock для двух фоновых потребителей OCR.
    """

    def __init__(self) -> None:
        self._engine: Any = None
        self._lock = threading.Lock()

    def recognize(self, rgb: NDArray[np.uint8]) -> list[TextObservation]:
        if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8 or not rgb.size:
            raise ValueError("OCR ожидает непустой RGB uint8 crop")
        height, width = rgb.shape[:2]
        with self._lock:
            if self._engine is None:
                from rapidocr_onnxruntime import RapidOCR

                self._engine = RapidOCR(
                    intra_op_num_threads=2,
                    inter_op_num_threads=1,
                    use_cls=False,
                    det_use_cuda=False,
                    rec_use_cuda=False,
                    det_limit_side_len=960,
                    det_limit_type="max",
                )
                logger.info("Загружена общая OCR-модель PP-OCRv4 на CPU")
            result, _elapsed = self._engine(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), use_cls=False)
        observations = []
        for coordinates, text, score in result or []:
            points = np.asarray(coordinates, dtype=float)
            x1, y1 = np.maximum(points.min(axis=0), (0, 0))
            x2, y2 = np.minimum(points.max(axis=0), (width, height))
            if x2 > x1 and y2 > y1:
                observations.append(
                    TextObservation(
                        text=text,
                        score=float(score),
                        box=NormalizedBox(
                            x1=x1 / width, y1=y1 / height, x2=x2 / width, y2=y2 / height
                        ),
                    )
                )
        return observations


_shared_provider = RapidOCRProvider()


def shared_ocr() -> OCRProvider:
    return _shared_provider
