"""
HP/MP самона по отдельной панели с проверкой видимости её иконки.
"""

import cv2
import numpy as np

from hid_pilot.bars import BarBox, BarProfile, SummonProfile, bar_fraction


def calibrate_summon(image: np.ndarray, hp: BarBox, mp: BarBox, icon: BarBox) -> SummonProfile:
    height, width = image.shape[:2]
    cropper = BarProfile(width=width, height=height)
    for box in (hp, mp):
        bar = cropper.crop(image, box)
        if bar.shape[0] < 3 or bar.shape[1] < 20 or bar.shape[1] / bar.shape[0] < 4:
            raise ValueError("Выделите полную горизонтальную полосу самона, без рамки")
    sample = cropper.crop(image, icon)
    if min(sample.shape[:2]) < 8:
        raise ValueError("Выделите иконку лапы самона, не меньше 8×8 px")
    pixels = cv2.resize(cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY), (32, 32),
                        interpolation=cv2.INTER_AREA)
    return SummonProfile(hp=hp, mp=mp, icon=icon, icon_pixels=pixels.tolist())


def read_summon(image: np.ndarray, profile: BarProfile) -> dict[str, float | None]:
    """
    Читает только текущий кадр; скрытая или перекрытая панель не даёт старых ресурсов.
    """
    result: dict[str, float | None] = {"summon_hp": None, "summon_mp": None}
    regions = profile.summon
    if regions is None or image.shape[:2] != (profile.height, profile.width):
        return result
    icon = profile.crop(image, regions.icon)
    if min(icon.shape[:2]) < 8:
        return result
    actual = cv2.resize(cv2.cvtColor(icon, cv2.COLOR_BGR2GRAY), (32, 32),
                        interpolation=cv2.INTER_AREA)
    if actual.std() < 10:
        return result
    expected = np.asarray(regions.icon_pixels, dtype=np.uint8)
    score = float(cv2.matchTemplate(actual, expected, cv2.TM_CCOEFF_NORMED)[0, 0])
    if not np.isfinite(score) or score < .92:
        return result
    for kind in ("hp", "mp"):
        crop = profile.crop(image, getattr(regions, kind))
        value = bar_fraction(crop, kind)
        if value is None and crop.shape[0] >= 3 and crop.shape[1] >= 20:
            hue, saturation, brightness = cv2.split(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV))
            color = ((hue <= 12) | (hue >= 165)) if kind == "hp" else ((hue >= 90) & (hue <= 135))
            # Иконка подтверждает панель; остаток цвета с ошибкой формы оставляем неизвестным.
            if not np.any(color & (saturation >= 85) & (brightness >= 45)):
                value = 0.
        result[f"summon_{kind}"] = value
    return result
