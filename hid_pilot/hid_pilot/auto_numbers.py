"""
Предложения числовых ROI по OCR-парам и соседним HP/MP/CP меткам.
"""

import numpy as np
from pydantic import BaseModel, ConfigDict

from hid_pilot.bars import BarBox
from hid_pilot.numbers import NumbersReader, ResourceNumbers, parse_numbers


class NumberCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)
    box: BarBox
    numbers: ResourceNumbers
    kind: str | None = None
    evidence: str


def find_numbers(image: np.ndarray, reader: NumbersReader, top_only: bool = True
                 ) -> list[NumberCandidate]:
    height, width = image.shape[:2]
    scan = image[:max(1, round(height * .30))] if top_only else image
    texts = reader.detect(scan)
    labels = []
    for points, text, score in texts:
        clean = text.strip().upper()
        if clean in {"HP", "MP", "CP"} and score >= .85:
            xy = np.asarray(points)
            labels.append((clean.lower(), xy.min(axis=0), xy.max(axis=0)))
    candidates = []
    for points, text, score in texts:
        detected = parse_numbers(text, score)
        if detected is None:
            continue
        xy = np.asarray(points)
        low, high = xy.min(axis=0), xy.max(axis=0)
        x1, y1 = max(0, int(low[0]) - 3), max(0, int(low[1]) - 3)
        x2, y2 = min(width, int(high[0]) + 4), min(height, int(high[1]) + 4)
        checked = reader.read(image[y1:y2, x1:x2])
        if checked is None or (checked.current, checked.maximum) != (
            detected.current, detected.maximum
        ):
            continue
        nearby = [label for label, a, b in labels if b[0] <= low[0] + 3
                  and low[0] - b[0] <= width * .3
                  and abs((a[1] + b[1] - low[1] - high[1]) / 2)
                  <= max(4, (high[1] - low[1]) * .65)]
        kind = nearby[0] if len(nearby) == 1 else None
        evidence = f"Метка {kind.upper()} в той же строке" if kind else "Нет однозначной метки"
        candidates.append(NumberCandidate(box=BarBox(x1=x1 / width, y1=y1 / height,
                                                     x2=x2 / width, y2=y2 / height),
                                          numbers=checked, kind=kind, evidence=evidence))
    # Единственная немаркированная пара справа от панели персонажа — предложение цели.
    own = [candidate for candidate in candidates if candidate.kind in {"hp", "mp"}]
    others = [i for i, candidate in enumerate(candidates) if candidate.kind is None]
    if own and len(others) == 1:
        index = others[0]
        if candidates[index].box.x1 > max(item.box.x2 for item in own):
            candidates[index] = candidates[index].model_copy(update={
                "kind": "target_hp", "evidence": "Единственная пара справа от панели HP/MP; подтвердите цель"
            })
    return candidates
