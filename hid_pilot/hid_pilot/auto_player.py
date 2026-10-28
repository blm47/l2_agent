"""
Предложения панели игрока по вертикальным строкам ника, класса и клана.
"""

import numpy as np
from pydantic import BaseModel

from hid_pilot.bars import BarBox
from hid_pilot.numbers import NumbersReader


class PlayerCandidate(BaseModel):
    box: BarBox
    evidence: str


def find_player_panels(image: np.ndarray, reader: NumbersReader, top_only: bool = True,
                       target_hp: BarBox | None = None) -> list[PlayerCandidate]:
    height, width = image.shape[:2]
    scan = image[:max(1, round(height * .3))] if top_only else image
    lines = []
    for points, text, score in reader.detect(scan):
        clean = text.strip()
        if score < .85 or not any(char.isalpha() for char in clean) or "/" in clean:
            continue
        if clean.upper() in {"HP", "MP", "CP"}:
            continue
        xy = np.asarray(points)
        lines.append((xy.min(axis=0), xy.max(axis=0), clean, score))
    lines.sort(key=lambda line: line[0][1])
    candidates = []
    for first in lines:
        group = [first]
        for line in lines:
            previous = group[-1]
            row_height = max(5, previous[1][1] - previous[0][1], line[1][1] - line[0][1])
            gap = line[0][1] - previous[0][1]
            if row_height * .8 <= gap <= row_height * 3.5 and abs(line[0][0] - first[0][0]) <= row_height * 3:
                group.append(line)
                if len(group) == 3:
                    break
        if len(group) != 3 or any(char.isdigit() for char in group[1][2]):
            continue
        low = np.min([line[0] for line in group], axis=0)
        high = np.max([line[1] for line in group], axis=0)
        box = BarBox(x1=max(0, low[0] - 3) / width, y1=max(0, low[1] - 3) / height,
                     x2=min(width, high[0] + 3) / width, y2=min(height, high[1] + 3) / height)
        candidates.append(PlayerCandidate(box=box.expanded_horizontal(0., .6), evidence=" / ".join(line[2] for line in group)
                                          + f" · OCR {min(line[3] for line in group):.0%}; подтвердите игрока"))
    if target_hp is not None:
        # Имя монстра занимает ту же область, что ник игрока, но может быть одной строкой.
        headers = [line for line in lines
                   if target_hp.y1 * height - height * .08 <= line[0][1] < line[1][1] <= target_hp.y1 * height
                   and line[0][0] >= (target_hp.x1 - .12) * width
                   and line[1][0] <= (target_hp.x2 + .04) * width]
        if headers:
            low = np.min([line[0] for line in headers], axis=0)
            high = np.max([line[1] for line in headers], axis=0)
            box = BarBox(x1=max(0, low[0] - 3) / width, y1=max(0, low[1] - 3) / height,
                         x2=min(width, high[0] + 3) / width,
                         y2=min(height, high[1] + 3, target_hp.y1 * height - 1) / height)
            candidates.append(PlayerCandidate(box=box.expanded_horizontal(0., .6), evidence="Общая область имени над HP: "
                                              + " / ".join(line[2] for line in headers) + "; подтвердите цель"))
    return candidates
