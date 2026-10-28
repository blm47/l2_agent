"""
Предложения HP/MP самона или пета для проверки пользователем.
"""

import cv2
import numpy as np

from hid_pilot.bars import BarBox


def find_summon_bars(image: np.ndarray) -> list[dict[str, BarBox]]:
    """
    Ищет пары полос без CP; полную длину пользователь проверяет на снимке.
    """
    height, width = image.shape[:2]
    hue, saturation, brightness = cv2.split(cv2.cvtColor(image, cv2.COLOR_BGR2HSV))
    colors = {"hp": (hue <= 12) | (hue >= 165),
              "mp": (hue >= 90) & (hue <= 135), "cp": (hue >= 15) & (hue <= 40)}
    bars = {}
    for kind, color in colors.items():
        mask = (color & (saturation >= 85) & (brightness >= 45)).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, 13), np.uint8))
        _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        bars[kind] = [(int(x), int(y), int(w), int(h)) for x, y, w, h, area in stats[1:]
                      if 20 <= w <= width * .5 and 3 <= h <= 25 and w / h >= 4
                      and area / (w * h) >= .8]
    candidates = []
    for x, y, w, h in bars["hp"]:
        # Верхние панели и строки пати с CP не предлагаем как пета.
        if y < height * .3 or any(abs(cx - x) <= 5 and 0 <= y - cy - ch <= 10
                                 for cx, cy, cw, ch in bars["cp"]):
            continue
        for mx, my, mw, mh in bars["mp"]:
            if abs(mx - x) > 3 or not 0 <= my - y - h <= 12:
                continue
            right = max(x + w, mx + mw)
            candidates.append({
                "hp": BarBox(x1=x / width, y1=y / height, x2=right / width, y2=(y + h) / height),
                "mp": BarBox(x1=mx / width, y1=my / height, x2=right / width, y2=(my + mh) / height),
            })
    return candidates
