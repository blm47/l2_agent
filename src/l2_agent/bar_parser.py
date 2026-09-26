import time

import cv2
import numpy as np
from numpy.typing import NDArray

from l2_agent.capture import CapturedFrame
from l2_agent.roi import BarName, RoiProfile
from l2_agent.world_state import BarObservation, PlayerState, WorldState

MAX_FRAME_AGE = 1.0


def parse_bar(rgb: NDArray[np.uint8], kind: BarName) -> BarObservation:
    """
    Читаем заполнение слева направо внутри подтверждённой полной длины полосы.

    Confidence — эвристическая оценка, не вероятность. Пустой crop не доказывает 0 HP.
    """
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("Ожидается RGB uint8 crop")
    height, width = rgb.shape[:2]
    if height < 3 or width < 20 or width / height < 4:
        return BarObservation(reason="ROI слишком мал или не похож на полосу")
    hue, saturation, value = cv2.split(cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV))
    if kind in ("hp", "target_hp"):
        color = (hue <= 12) | (hue >= 165)
    elif kind == "mp":
        color = (hue >= 90) & (hue <= 135)
    elif kind == "cp":
        color = (hue >= 17) & (hue <= 40)
    else:
        raise ValueError("Неизвестная полоса")
    mask = color & (saturation >= 85) & (value >= 45)
    columns = np.mean(mask, axis=0) >= 0.35
    indices = np.flatnonzero(columns)
    if len(indices) < 3:
        return BarObservation(reason="Цвет полосы не виден; нулевое значение не подтверждено")
    start, end = int(indices[0]), int(indices[-1]) + 1
    if start > max(2, int(width * 0.05)):
        return BarObservation(reason="Заполнение не начинается у левого края ROI")
    # Небольшие разрывы от текста допустимы; отдельные цветные объекты — нет.
    gaps = np.diff(indices) - 1
    if len(gaps) and int(gaps.max()) > max(3, int(width * 0.04)):
        return BarObservation(reason="Заполнение неоднозначно: крупные разрывы")
    support = float(np.mean(mask[:, start:end]))
    if support < 0.45 or np.count_nonzero(np.mean(mask[:, start:end], axis=1) >= 0.6) < 2:
        return BarObservation(reason="Недостаточно согласованных строк полосы")
    return BarObservation(
        value=end / width,
        confidence=min(0.85, 0.55 + support * 0.3),
        reason="Оценка цветового заполнения подтверждённого ROI",
    )


def perceive_bars(
    frame: CapturedFrame, profile: RoiProfile | None, *, now: float | None = None
) -> WorldState:
    """
    Строим снимок из текущего кадра, не переносим значения из предыдущих кадров.
    """
    now = time.monotonic() if now is None else now
    height, width = frame.rgb.shape[:2]
    reason = None
    if not 0 <= now - frame.timestamp <= MAX_FRAME_AGE:
        reason = "Кадр устарел или имеет некорректное время"
    elif (width, height) != (frame.rect.width, frame.rect.height):
        reason = "Размер изображения не совпадает с клиентской областью"
    elif profile is None:
        reason = "Нужна подтверждённая настройка ROI"
    elif not profile.matches(width, height):
        reason = "Размер кадра изменился; нужна новая настройка ROI"
    bars: dict[BarName, BarObservation] = {}
    for kind in ("hp", "mp", "cp", "target_hp"):
        if reason is not None:
            bars[kind] = BarObservation(reason=reason)
        elif profile is None or kind not in profile.bars:
            bars[kind] = BarObservation(reason="ROI не настроен")
        else:
            x1, y1, x2, y2 = profile.bars[kind].pixels(width, height)
            bars[kind] = parse_bar(frame.rgb[y1:y2, x1:x2], kind)
    return WorldState(
        frame_id=frame.sequence,
        timestamp=frame.timestamp,
        player=PlayerState(hp=bars["hp"], mp=bars["mp"], cp=bars["cp"]),
        target_hp=bars["target_hp"],
    )
