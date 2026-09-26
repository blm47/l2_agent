import hashlib
import logging
import threading
import time
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from l2_agent.capture import CapturedFrame
from l2_agent.ocr import OCRProvider, TextObservation, shared_ocr
from l2_agent.roi import RoiProfile
from l2_agent.world_state import NameObservation

logger = logging.getLogger("l2_agent.target_ocr")
NAME_MAX_AGE = 1.5


@lru_cache(maxsize=1)
def _close_icon() -> NDArray[np.uint8]:
    image = cv2.imread(str(Path(__file__).parent / "assets" / "lu4-target-close.png"), 0)
    if image is None:
        raise OSError("Не найден шаблон панели цели LU4")
    return image


def target_panel_visible(frame: CapturedFrame, profile: RoiProfile) -> bool:
    """
    Проверяем кнопку закрытия панели независимо от наличия красной HP-полосы.
    """
    height, width = frame.rgb.shape[:2]
    if not profile.matches(width, height) or "target_hp" not in profile.bars:
        return False
    _x1, y1, x2, y2 = profile.bars["target_hp"].pixels(width, height)
    bar_height = y2 - y1
    region = frame.rgb[
        max(0, y1 - 6 * bar_height) : max(0, y1 - 2 * bar_height), max(0, x2 - 8 * bar_height) : x2
    ]
    template = cv2.resize(_close_icon(), None, fx=bar_height / 15, fy=bar_height / 15)
    if region.shape[0] < template.shape[0] or region.shape[1] < template.shape[1]:
        return False
    scores = cv2.matchTemplate(
        cv2.cvtColor(region, cv2.COLOR_RGB2GRAY), template, cv2.TM_CCOEFF_NORMED
    )
    return bool(np.max(scores) >= 0.85)


def text_fingerprint(crop: NDArray[np.uint8]) -> bytes:
    # Белые буквы стабильны; полупрозрачный фон и цветные иконки меняются постоянно.
    low = crop.min(axis=2)
    spread = crop.max(axis=2).astype(np.int16) - low
    mask = (low >= 150) & (spread <= 45)
    return hashlib.blake2b(mask.tobytes(), digest_size=16).digest()


def target_header(frame: CapturedFrame, profile: RoiProfile) -> NDArray[np.uint8] | None:
    """
    Заголовок стандартной панели LU4 над подтверждённой полосой HP цели.
    """
    height, width = frame.rgb.shape[:2]
    if not profile.matches(width, height) or "target_hp" not in profile.bars:
        return None
    x1, y1, x2, y2 = profile.bars["target_hp"].pixels(width, height)
    bar_height = y2 - y1
    top, bottom = y1 - 5 * bar_height, y1 - 3 * bar_height
    if top < 0 or bottom <= top or x2 - x1 < 20:
        return None
    # Исключаем аватар слева и служебные иконки справа от текста.
    left, right = x1 + 2 * bar_height, x2 - 4 * bar_height
    if right - left < 20:
        return None
    return frame.rgb[top:bottom, left:right].copy()


def select_name(lines: list[TextObservation], frame_id: int, timestamp: float) -> NameObservation:
    candidates = []
    for line in lines:
        text = " ".join(line.text.split())
        if (
            line.score >= 0.85
            and 2 <= len(text) <= 80
            and sum(char.isalpha() for char in text) >= 2
            and all(char.isalnum() or char in " '-’" for char in text)
        ):
            candidates.append((text, line.score))
    if len(candidates) != 1:
        return NameObservation(reason="Имя не прочитано однозначно")
    text, score = candidates[0]
    return NameObservation(
        value=text,
        confidence=score,
        frame_id=frame_id,
        timestamp=timestamp,
        reason="OCR заголовка цели; тип сущности не определён",
    )


class TargetNameReader:
    """
    Один OCR job/crop и один результат; GUI не ждёт распознавания.
    """

    def __init__(self, provider: OCRProvider | None = None) -> None:
        self.provider = provider or shared_ocr()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._generation = 0
        self._key: object = None
        self._result: tuple[int, NameObservation] | None = None
        self._cached: NameObservation | None = None
        self._next_submit = 0.0

    def invalidate(self) -> None:
        with self._lock:
            if self._key is not None:
                self._generation += 1
            self._key = None
            self._cached = None
            self._result = None

    def observe(
        self,
        frame: CapturedFrame,
        profile: RoiProfile | None,
        enabled: bool,
        context: object,
    ) -> NameObservation:
        now = time.monotonic()
        if profile is None or not enabled or not 0 <= now - frame.timestamp <= 1:
            self.invalidate()
            return NameObservation(reason="Нет свежего кадра или подтверждённого ROI цели")
        if not target_panel_visible(frame, profile):
            self.invalidate()
            return NameObservation(reason="Панель цели не видна")
        crop = target_header(frame, profile)
        if crop is None:
            self.invalidate()
            return NameObservation(reason="Не удалось определить область имени цели")
        # При изменении букв/окна прежнее имя сразу становится неизвестным.
        key = (
            context,
            frame.rect,
            profile.bars["target_hp"],
            text_fingerprint(crop),
        )
        with self._lock:
            if self._closed:
                return NameObservation(reason="OCR остановлен")
            if key != self._key:
                self._key = key
                self._generation += 1
                self._cached = None
            if self._result is not None:
                generation, result = self._result
                self._result = None
                if generation == self._generation:
                    self._cached = result
            if now >= self._next_submit and (self._thread is None or not self._thread.is_alive()):
                self._next_submit = now + 1.0
                self._thread = threading.Thread(
                    target=self._run,
                    args=(self._generation, crop, frame.sequence, frame.timestamp),
                    name="TargetOCR",
                    daemon=True,
                )
                self._thread.start()
            cached = self._cached
            if cached is not None:
                if cached.value is None:
                    return cached
                if cached.timestamp is not None and 0 <= now - cached.timestamp <= NAME_MAX_AGE:
                    return cached
        return NameObservation(reason="Ожидание свежего OCR имени цели")

    def _run(
        self, generation: int, crop: NDArray[np.uint8], frame_id: int, timestamp: float
    ) -> None:
        failed = False
        try:
            enlarged = cv2.resize(crop, None, fx=2, fy=2)
            result = select_name(self.provider.recognize(enlarged), frame_id, timestamp)
        except Exception:
            failed = True
            logger.exception("Не удалось прочитать имя цели")
            result = NameObservation(reason="OCR недоступен; подробности в логе")
        with self._lock:
            if not self._closed:
                self._result = generation, result
                if failed:
                    self._next_submit = time.monotonic() + 10

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._result = None
            self._cached = None
        if self._thread is not None:
            self._thread.join(timeout=2)
