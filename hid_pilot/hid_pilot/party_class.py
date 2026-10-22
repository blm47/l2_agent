"""
Распознавание размеченных иконок класса по эталонам без OCR и нейросети.
"""

import cv2
import numpy as np

from hid_pilot.bars import BarProfile
from hid_pilot.party import PartyMember, PartyProfile


def icon_pixels(image: np.ndarray) -> list[list[int]]:
    if image.size == 0 or min(image.shape[:2]) < 8:
        raise ValueError("Выделите иконку класса размером не менее 8×8 пикселей")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Убираем рамку и цифры уровня у нижнего края иконки.
    height, width = gray.shape
    crop = gray[round(height * .12):round(height * .78), round(width * .15):round(width * .85)]
    resized = cv2.resize(crop, (32, 32), interpolation=cv2.INTER_AREA)
    if float(resized.std()) < 10:
        raise ValueError("В выделении нет достаточно различимой иконки класса")
    return resized.tolist()


class PartyClassDetector:
    def __init__(self, profile: PartyProfile) -> None:
        self.profile = profile
        self.templates = {name: np.asarray(pixels, dtype=np.uint8)
                          for name, pixels in profile.class_templates.items()}
        self.pending: dict[int, tuple[str | None, str, int, float]] = {}

    def apply(self, image: np.ndarray, members: list[PartyMember], at: float) -> list[PartyMember]:
        if self.profile.class_icon is None or not self.templates:
            return members
        cropper = BarProfile(width=self.profile.width, height=self.profile.height)
        result = []
        active = {member.slot for member in members}
        self.pending = {slot: value for slot, value in self.pending.items() if slot in active}
        for member in members:
            label, score = None, 0.
            try:
                crop = cropper.crop(image, self.profile.box("class_icon", member.slot - 1))
                pixels = np.asarray(icon_pixels(crop), dtype=np.uint8)
                scores = sorted(((float(cv2.matchTemplate(pixels, template, cv2.TM_CCOEFF_NORMED)[0, 0]), name)
                                 for name, template in self.templates.items()), reverse=True)
                if scores[0][0] >= .94 and (len(scores) == 1 or scores[0][0] - scores[1][0] >= .04):
                    score, label = scores[0]
            except ValueError:
                pass
            previous = self.pending.get(member.slot)
            count = 1
            if (label and previous and previous[0] == member.name and previous[1] == label
                    and 0 <= at - previous[3] <= 2):
                count = previous[2] + int(at > previous[3])
            elif previous and at <= previous[3]:
                # Повтор одного кадра не подтверждает класс заново.
                label = None
            if label:
                self.pending[member.slot] = member.name, label, count, at
            else:
                self.pending.pop(member.slot, None)
            result.append(member.model_copy(update={"player_class": label if count >= 3 else None,
                                                    "class_confidence": min(1., score) if count >= 3 else 0.,
                                                    "class_icon_checked": True}))
        return result
