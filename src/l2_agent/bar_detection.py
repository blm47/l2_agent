import logging
import re

import cv2
import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

from l2_agent.ocr import OCRProvider, TextObservation, shared_ocr
from l2_agent.roi import BarName, NormalizedBox, RoiProfile

logger = logging.getLogger("l2_agent.bar_detection")


class BarCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: BarName
    box: NormalizedBox
    color_support: float = Field(ge=0, le=1)


class BarProposal(BaseModel):
    model_config = ConfigDict(frozen=True)
    profile: RoiProfile
    evidence: list[str]
    # Это балл согласованности признаков, а не вероятность правильного ответа.
    score: float = Field(ge=0, le=1)
    needs_confirmation: bool = True
    target_options: list[NormalizedBox] = Field(default_factory=list)


class DetectionResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    proposals: list[BarProposal] = Field(default_factory=list)
    message: str


def colored_candidates(rgb: NDArray[np.uint8]) -> list[BarCandidate]:
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError("Ожидается RGB uint8 кадр")
    height, width = rgb.shape[:2]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    hue, saturation, value = cv2.split(hsv)
    masks: dict[BarName, NDArray[np.bool_]] = {
        "hp": ((hue <= 12) | (hue >= 165)),
        "mp": ((hue >= 90) & (hue <= 135)),
        "cp": ((hue >= 17) & (hue <= 40)),
    }
    found = []
    for kind, color in masks.items():
        mask = (color & (saturation >= 85) & (value >= 45)).astype(np.uint8) * 255
        # Соединяем разрывы от цифр, но не объединяем соседние строки полос.
        connected = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((1, 9), np.uint8))
        count, _labels, stats, _centers = cv2.connectedComponentsWithStats(connected)
        for index in range(1, count):
            x, y, w, h, _area = (int(v) for v in stats[index])
            if not (
                max(24, width * 0.018) <= w <= width * 0.5 and 3 <= h <= min(40, height * 0.06)
            ):
                continue
            if w / h < 4:
                continue
            support = float(np.count_nonzero(mask[y : y + h, x : x + w])) / (w * h)
            if support < 0.3:
                continue
            row_coverage = np.mean(mask[y : y + h, x : x + w] > 0, axis=1)
            if np.count_nonzero(row_coverage >= 0.8) < max(2, h * 0.2):
                continue
            found.append(
                BarCandidate(
                    kind=kind,
                    color_support=support,
                    box=NormalizedBox(
                        x1=x / width,
                        y1=y / height,
                        x2=(x + w) / width,
                        y2=(y + h) / height,
                    ),
                )
            )
    return sorted(found, key=lambda item: item.color_support, reverse=True)[:60]


def label_matches(candidate: BarCandidate, text: TextObservation, width: int, height: int) -> bool:
    normalized = re.sub(r"\s+", "", text.text.upper())
    if not re.match(rf"^{candidate.kind.upper()}(?:[^A-Z]|$)", normalized):
        return False
    x1, y1, x2, y2 = candidate.box.pixels(width, height)
    tx1, ty1, tx2, ty2 = text.box.pixels(width, height)
    return (
        abs((ty1 + ty2 - y1 - y2) / 2) <= max(10, y2 - y1)
        and tx1 <= x2
        and tx2 >= x1 - max(70, (x2 - x1) * 0.4)
    )


def build_proposals(
    candidates: list[BarCandidate], texts: list[TextObservation], width: int, height: int
) -> DetectionResult:
    proposals = []
    for hp in (item for item in candidates if item.kind == "hp"):
        hx, hy, hr, hb = hp.box.pixels(width, height)
        group: dict[BarName, BarCandidate] = {"hp": hp}
        ambiguous = False
        for kind in ("mp", "cp"):
            nearby = []
            for item in candidates:
                if item.kind != kind:
                    continue
                x, y, _right, bottom = item.box.pixels(width, height)
                if (
                    abs(x - hx) <= max(16, (hr - hx) * 0.12)
                    and abs((y + bottom - hy - hb) / 2) <= max(70, (hb - hy) * 6)
                    and (bottom <= hy or y >= hb)
                ):
                    nearby.append(item)
            if nearby:
                nearby.sort(key=lambda item: abs(item.box.y1 - hp.box.y1))
                group[nearby[0].kind] = nearby[0]
                ambiguous |= len(nearby) > 1
        # Одиночный красный объект не является достаточным признаком панели игрока.
        if "mp" not in group:
            continue
        evidence = ["Рядом найдены горизонтальные красная и синяя полосы"]
        if "cp" in group:
            evidence.append("Найдена соседняя жёлтая полоса")
        labels = {
            kind: max(
                (text.score for text in texts if label_matches(item, text, width, height)),
                default=0,
            )
            for kind, item in group.items()
        }
        for kind, confidence in labels.items():
            if confidence >= 0.8:
                evidence.append(f"OCR прочитал {kind.upper()} рядом с полосой")
        # Цвет показывает заполнение, но не гарантирует видимость правого края.
        # Общая максимальная длина — гипотеза до подтверждения пользователем.
        left = min(item.box.x1 for item in group.values())
        right_edge = max(item.box.x2 for item in group.values())
        bars = {}
        for kind, item in group.items():
            # В LU4 правый край панели скошен: полные CP/HP/MP имеют разную длину.
            # Только явная дробь N/N в этой строке поддерживает собственный край.
            full = False
            x1, y1, x2, y2 = item.box.pixels(width, height)
            for text in texts:
                match = re.search(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)", text.text)
                tx1, ty1, tx2, ty2 = text.box.pixels(width, height)
                if (
                    match is not None
                    and text.score >= 0.9
                    and int(match[1]) > 0
                    and int(match[1]) == int(match[2])
                    and x1 <= (tx1 + tx2) / 2 <= x2
                    and abs((ty1 + ty2 - y1 - y2) / 2) <= max(3, (y2 - y1) * 0.6)
                ):
                    full = True
                    break
            bars[kind] = NormalizedBox(
                x1=item.box.x1 if full else left,
                y1=item.box.y1,
                x2=item.box.x2 if full else right_edge,
                y2=item.box.y2,
            )
            if full:
                evidence.append(f"OCR: {kind.upper()} заполнена; учтён собственный правый край")
        score = min(
            0.9,
            0.45
            + (0.15 if "cp" in group else 0)
            + 0.1 * sum(value >= 0.8 for value in labels.values()),
        )
        if ambiguous:
            score = min(score, 0.5)
            evidence.append("Несколько похожих полос — назначение неоднозначно")
        evidence.append("Полная длина полос требует подтверждения перед вычислением процентов")
        proposals.append(
            BarProposal(
                profile=RoiProfile(frame_width=width, frame_height=height, bars=bars),
                evidence=evidence,
                score=score,
                target_options=[
                    item.box for item in candidates if item.kind == "hp" and item.box != hp.box
                ][:5],
            )
        )
    proposals.sort(key=lambda item: item.score, reverse=True)
    return DetectionResult(
        proposals=proposals[:5],
        message=(
            "Это полосы вашего персонажа? Рамки охватывают всю длину, включая пустую часть?"
            if proposals
            else "Не удалось уверенно найти панель HP/MP. Видна ли игра и панель персонажа?"
        ),
    )


class AutoBarDetector:
    def __init__(self) -> None:
        self._ocr: OCRProvider | None = None

    def detect(self, rgb: NDArray[np.uint8]) -> DetectionResult:
        candidates = colored_candidates(rgb)
        height, width = rgb.shape[:2]
        preliminary = build_proposals(candidates, [], width, height)
        if not preliminary.proposals:
            return preliminary
        if self._ocr is None:
            self._ocr = shared_ocr()
        # Читаем только окрестности кандидатов, а не весь чат/рабочий стол.
        texts = []
        for proposal in preliminary.proposals:
            bounds = [box.pixels(width, height) for box in proposal.profile.bars.values()]
            left = max(0, min(box[0] for box in bounds) - 70)
            top = max(0, min(box[1] for box in bounds) - 20)
            right = min(width, max(box[2] for box in bounds) + 40)
            bottom = min(height, max(box[3] for box in bounds) + 20)
            crop = cv2.resize(rgb[top:bottom, left:right], None, fx=2, fy=2)
            for text in self._ocr.recognize(crop):
                x1 = left + text.box.x1 * (right - left)
                y1 = top + text.box.y1 * (bottom - top)
                x2 = left + text.box.x2 * (right - left)
                y2 = top + text.box.y2 * (bottom - top)
                if x2 > x1 and y2 > y1:
                    texts.append(
                        TextObservation(
                            text=text.text,
                            score=text.score,
                            box=NormalizedBox(
                                x1=float(x1 / width),
                                y1=float(y1 / height),
                                x2=float(x2 / width),
                                y2=float(y2 / height),
                            ),
                        )
                    )
        return build_proposals(candidates, texts, width, height)
