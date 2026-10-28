"""
Наблюдение до восьми строк пати: имена через OCR, ресурсы по полосам.
"""

from itertools import pairwise

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hid_pilot.bars import BarBox, BarProfile, bar_fraction
from hid_pilot.numbers import NumbersReader


class PartyProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    width: int = Field(ge=20)
    height: int = Field(ge=20)
    name: BarBox
    cp: BarBox
    hp: BarBox
    mp: BarBox
    row_step: int = Field(ge=20, le=500)
    row_offsets: list[int] = Field(default_factory=list, max_length=8)
    class_icon: BarBox | None = None
    class_templates: dict[str, list[list[int]]] = Field(default_factory=dict, max_length=2)

    @model_validator(mode="after")
    def rows_fit(self) -> "PartyProfile":
        boxes = (self.name, self.cp, self.hp, self.mp) + ((self.class_icon,) if self.class_icon else ())
        for label, pixels in self.class_templates.items():
            if (label not in {"Elven Elder", "Shilien Elder"} or len(pixels) != 32
                    or any(len(row) != 32 or any(not 0 <= pixel <= 255 for pixel in row) for row in pixels)):
                raise ValueError("Эталон класса должен содержать 32×32 пикселя для ЕЕ или ШЕ")
            if float(np.asarray(pixels).std()) < 10:
                raise ValueError("Эталон иконки класса не должен быть однотонным")
        offsets = self.row_offsets or [slot * self.row_step for slot in range(8)]
        if len(offsets) != 8 or offsets[0] != 0:
            raise ValueError("Нужны восемь смещений строк, первое — 0")
        gaps = [b - a for a, b in pairwise(offsets)]
        if min(gaps) < 20:
            raise ValueError("Строки пати должны идти сверху вниз с шагом не менее 20 px")
        if max(b.y2 for b in boxes) - min(b.y1 for b in boxes) > min(gaps) / self.height:
            raise ValueError("Шаг строк меньше высоты участника")
        if max(b.y2 for b in boxes) + offsets[-1] / self.height > 1:
            raise ValueError("Восемь строк пати выходят за границы окна")
        return self

    def box(self, name: str, slot: int) -> BarBox:
        if not 0 <= slot < 8:
            raise ValueError("Номер строки пати вне диапазона 0–7")
        base = getattr(self, name)
        offset = (self.row_offsets[slot] if self.row_offsets else slot * self.row_step) / self.height
        return BarBox(x1=base.x1, x2=base.x2, y1=base.y1 + offset, y2=base.y2 + offset)


class PartyMember(BaseModel):
    model_config = ConfigDict(frozen=True)
    slot: int = Field(ge=1, le=8)
    name: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    cp: float | None = Field(default=None, ge=0, le=1)
    hp: float | None = Field(default=None, ge=0, le=1)
    mp: float | None = Field(default=None, ge=0, le=1)
    player_class: str | None = None
    class_confidence: float = Field(default=0, ge=0, le=1)
    class_icon_checked: bool = False

    @property
    def target_id(self) -> str:
        return f"@slot{self.slot}"


def read_party_bars(image: np.ndarray, profile: PartyProfile,
                    metadata: list[PartyMember] | None = None) -> list[PartyMember]:
    """
    Читает ресурсы текущего кадра без OCR, сохраняя проверенную идентичность строк.
    """
    cropper = BarProfile(width=profile.width, height=profile.height)
    known = {member.slot: member for member in metadata or []}
    members = []
    for slot in range(8):
        values = {kind: bar_fraction(cropper.crop(image, profile.box(kind, slot)), kind)
                  for kind in ("cp", "hp", "mp")}
        member = known.get(slot + 1)
        present = sum(value is not None for value in values.values()) >= 2
        if not present and (member is None or not member.name):
            continue
        if member is None:
            member = PartyMember(slot=slot + 1)
        members.append(member.model_copy(update=values))
    return members


def read_party(image: np.ndarray, profile: PartyProfile,
               reader: NumbersReader) -> list[PartyMember]:
    cropper = BarProfile(width=profile.width, height=profile.height)
    # Упаковываем только восемь строк имён: фон между участниками не нужен OCR.
    strips = [cropper.crop(image, profile.box("name", slot)) for slot in range(8)]
    texts = reader.detect(np.vstack(strips))
    members = []
    top = 0
    for slot in range(8):
        bottom = top + strips[slot].shape[0]
        names = [(text.strip(), score) for points, text, score in texts
                 if .85 <= score <= 1 and text.strip() and not text.strip().isdigit()
                 and top <= float(np.mean(np.asarray(points)[:, 1])) < bottom]
        top = bottom
        values = {kind: bar_fraction(cropper.crop(image, profile.box(kind, slot)), kind)
                  for kind in ("cp", "hp", "mp")}
        # Одна случайная цветная линия не подтверждает наличие участника.
        present_bars = sum(value is not None for value in values.values()) >= 2
        if not names and not present_bars:
            continue
        name, confidence = names[0] if len(names) == 1 else (None, 0.)
        members.append(PartyMember(slot=slot + 1, name=name, confidence=confidence, **values))
    return members


def party_text(members: list[PartyMember]) -> str:
    def percent(value: float | None) -> str:
        return "?" if value is None else f"{value:.0%}"

    lines = [f"Распознано участников: {len(members)}/8"]
    for member in members:
        lines.append(f"{member.slot}. CP {percent(member.cp)} · HP {percent(member.hp)} · "
                     f"MP {percent(member.mp)} · {member.name or 'имя неизвестно'}")
    if not members:
        lines.append("Строки не обнаружены: пати пуста либо панель скрыта/не распознана")
    return "\n".join(lines)
