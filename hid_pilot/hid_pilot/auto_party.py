"""
Предложения области пати по тройкам CP/HP/MP и OCR имени над ними.
"""

from itertools import pairwise

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict

from hid_pilot.bars import BarBox
from hid_pilot.numbers import NumbersReader
from hid_pilot.party import PartyProfile


class PartyCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)
    profile: PartyProfile
    evidence: str


def find_party(image: np.ndarray, reader: NumbersReader | None,
               region: BarBox | None = None) -> list[PartyCandidate]:
    height, width = image.shape[:2]
    hue, saturation, value = cv2.split(cv2.cvtColor(image, cv2.COLOR_BGR2HSV))
    colors = {"cp": (hue >= 15) & (hue <= 40),
              "hp": (hue <= 12) | (hue >= 165),
              "mp": (hue >= 90) & (hue <= 135)}
    bars = {}
    for kind, color in colors.items():
        mask = (color & (saturation >= 85) & (value >= 45)).astype(np.uint8)
        if region is not None:
            allowed = np.zeros_like(mask)
            allowed[round(region.y1 * height):round(region.y2 * height),
                    round(region.x1 * width):round(region.x2 * width)] = 1
            mask &= allowed
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, 13), np.uint8))
        _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        bars[kind] = [(int(x), int(y), int(w), int(h)) for x, y, w, h, area in stats[1:]
                      if (50 if kind == "cp" else 12) <= w <= width * .4
                      and 3 <= h <= 25 and w / h >= 4
                      and area / (w * h) >= .65]

    def box(x: int, y: int, w: int, h: int) -> BarBox:
        return BarBox(x1=x / width, y1=y / height, x2=(x + w) / width, y2=(y + h) / height)

    groups = []
    for cp in bars["cp"]:
        for hp in bars["hp"]:
            if not 0 <= hp[1] - (cp[1] + cp[3]) <= 8:
                continue
            for mp in bars["mp"]:
                # HP/MP могут быть заполнены частично; правую границу предлагает CP.
                if not (0 <= mp[1] - (hp[1] + hp[3]) <= 8
                        and all(abs(b[0] - cp[0]) <= max(12, cp[2] * .15)
                                and b[0] + b[2] <= cp[0] + cp[2] + 4 for b in (hp, mp))):
                    continue
                x, y, w, _ = cp
                top = max(0, y - max(25, 6 * cp[3]))
                texts = reader.detect(image[top:y - 2, x:x + w]) if reader else []
                if any("/" in text or text.strip().upper() in {"CP", "HP", "MP"}
                       for _, text, score in texts if score >= .85):
                    continue
                names = [text.strip() for _, text, score in texts if score >= .85
                         and text.strip() and "/" not in text and not text.strip().isdigit()
                         and text.strip().upper() not in {"CP", "HP", "MP"}]
                groups.append((cp, hp, mp, box(x, top, w, y - 2 - top),
                               names[0] if len(names) == 1 else None))
    results = []
    groups.sort(key=lambda group: group[0][1])
    for cp, hp, mp, name, text in sorted(groups, key=lambda group: group[0][1]):
        if any(abs(candidate.profile.cp.x2 * width - (cp[0] + cp[2])) < 5 for candidate in results):
            continue
        rows = [other for other in groups if other[0][1] >= cp[1]
                and abs(other[0][0] - cp[0]) < 8
                and abs(other[0][0] + other[0][2] - (cp[0] + cp[2])) < 5][:8]
        if not any(row[4] for row in rows) and region is None:
            continue
        offsets = [row[0][1] - cp[1] for row in rows]
        gaps = [b - a for a, b in pairwise(offsets)]
        step = min(gaps) if gaps else round(mp[1] + mp[3] - name.y1 * height + 10)
        while len(offsets) < 8:
            offsets.append(offsets[-1] + step)

        def full_bar(bar: tuple[int, int, int, int], right: int) -> BarBox:
            return box(bar[0], bar[1], right - bar[0], bar[3])

        try:
            profile = PartyProfile(width=width, height=height, name=name,
                                   cp=box(*cp), hp=full_bar(hp, cp[0] + cp[2]),
                                   mp=full_bar(mp, cp[0] + cp[2]),
                                   row_step=step, row_offsets=offsets)
        except ValueError:
            continue
        evidence = (f"{text or 'Имя неизвестно'}: сверху; строк {len(rows)}; шаг {step} px; "
                    "проверьте полную длину и отступы")
        results.append(PartyCandidate(profile=profile, evidence=evidence))
    return results
