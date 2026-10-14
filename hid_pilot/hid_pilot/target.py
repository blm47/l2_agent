"""
OCR подтверждённой панели цели и признаки её состояния без игрового ввода.
"""

import time
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from hid_pilot.bars import BarProfile, bar_fraction, red_marker_mask, text_mask
from hid_pilot.numbers import NumbersReader, parse_numbers, shared_numbers


def ocr_lines(rows: list[tuple[list[list[float]], str, float]]) -> list[tuple[float, str, float]]:
    fragments = []
    for points, text, score in rows:
        if score < .85 or not text.strip():
            continue
        xy = np.asarray(points)
        fragments.append((float(np.mean(xy[:, 1])), float(np.min(xy[:, 0])),
                          float(np.max(xy[:, 1]) - np.min(xy[:, 1])), " ".join(text.split()), float(score)))
    groups = []
    for fragment in sorted(fragments):
        if groups and abs(fragment[0] - groups[-1][0][0]) <= max(3, .45 * max(fragment[2], groups[-1][0][2])):
            groups[-1].append(fragment)
        else:
            groups.append([fragment])
    # OCR может разделять 0 / 58 на фрагменты: объединяем только одну горизонтальную строку.
    return [(group[0][0], " ".join(item[3] for item in sorted(group, key=lambda item: item[1])),
             min(item[4] for item in group)) for group in groups]


def target_text_signature(image: np.ndarray) -> bytes:
    low, high = np.min(image, axis=2), np.max(image, axis=2)
    colored = (high >= 125) & (high.astype(int) - low.astype(int) >= 65)
    return (text_mask(image) | colored).tobytes()


class TargetObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    at: float
    kind: Literal["player", "npc", "monster", "unknown"] = "unknown"
    name: str | None = None
    raw_name: str | None = None
    name_confirmations: int = Field(default=0, ge=0, le=6)
    level: int | None = Field(default=None, ge=1, le=150)
    player_class: str | None = None
    clan: str | None = None
    hp: float | None = Field(default=None, ge=0, le=1)
    dead: bool | None = None
    death_marker: bool = False
    confidence: float = Field(default=0, ge=0, le=1)
    epoch: int = 0


class TargetReader:
    def __init__(self, profile: BarProfile) -> None:
        self.profile = profile
        self.cached_signature: bytes | None = None
        self.cached: TargetObservation | None = None
        self.cached_config: tuple | None = None
        self.last_player_name: str | None = None
        self.last_monster_name: str | None = None

    def signature(self, image: np.ndarray) -> bytes:
        box = self.profile.target_panel or self.profile.target_player_panel
        if box is None:
            return b""
        # Учитываем цветные имена, но не заставляем OCR повторяться из-за тёмного игрового фона.
        crop = self.profile.crop(image, box)
        if crop.size == 0:
            return b""
        parts = [target_text_signature(crop)]
        if self.profile.target_level is not None:
            parts.append(target_text_signature(self.profile.crop(image, self.profile.target_level)))
        hp_box = (self.profile.target_hp_presence_box or self.profile.numbers.get("target_hp")
                  or self.profile.bars.get("target_hp"))
        if hp_box is not None:
            parts.append(target_text_signature(self.profile.crop(image, hp_box)))
        if self.profile.target_dead_marker is not None:
            marker_crop = self.profile.crop(image, self.profile.target_dead_marker)
            if min(marker_crop.shape[:2]) >= 3:
                parts.append(red_marker_mask(marker_crop).tobytes())
        return b"\x00\xff".join(parts)

    def read(self, image: np.ndarray, at: float | None = None,
             reader: NumbersReader | None = None) -> TargetObservation:
        at = time.monotonic() if at is None else at
        signature = self.signature(image)
        if not signature:
            # HP настраивается отдельно от имени: отсутствие панели не отменяет чтение HP.
            hp = None
            if "target_hp" in self.profile.bars.keys() | self.profile.numbers.keys():
                hp = self.profile.read(image, "target_hp")
            marker = self.profile.dead_marker_visible(image)
            return TargetObservation(at=at, hp=hp, dead=self.death(hp, marker), death_marker=marker,
                                     level=self.profile.read_level(image, "target_level", reader))
        marker = self.profile.dead_marker_visible(image)
        hp_box = (self.profile.target_hp_presence_box or self.profile.numbers.get("target_hp")
                  or self.profile.bars.get("target_hp"))
        configuration = (tuple(sorted(self.profile.target_kind_names.items())),
                         hp_box.model_dump_json() if hp_box else None)
        if (self.cached is not None and self.cached_signature == signature
                and self.cached_config == configuration):
            result = self.cached.model_copy(update={"at": at, "death_marker": marker,
                                                   "dead": self.death(self.cached.hp, marker)})
            return result
        box = self.profile.target_panel or self.profile.target_player_panel
        rows = ocr_lines((reader or shared_numbers()).detect(self.profile.crop(image, box)))
        numbers = [parse_numbers(text, score) for _, text, score in rows if "/" in text]
        numbers = [value for value in numbers if value is not None]
        names = [row for row in rows if any(char.isalpha() for char in row[1]) and "/" not in row[1]]
        hp = numbers[0].fraction if len(numbers) == 1 else None
        strip_present: bool | None = None
        if hp_box is not None:
            strip = self.profile.crop(image, hp_box)
            fill = bar_fraction(strip, "target_hp")
            mask = text_mask(strip)
            text_pixels = int(np.count_nonzero(mask))
            # Даже при пропуске OCR видимые цифры/цветная полоса не означают отсутствие HP.
            if strip.shape[0] >= 3 and strip.shape[1] >= 8:
                if fill is not None or 4 <= text_pixels <= mask.size * .5:
                    strip_present = True
                elif text_pixels < 4:
                    strip_present = False
            if hp is None and fill is not None:
                hp = fill
        if hp is None and "target_hp" in self.profile.numbers:
            # Имя находится выше HP и может быть размечено отдельным прямоугольником.
            numeric_hp = (reader or shared_numbers()).read(
                self.profile.crop(image, self.profile.numbers["target_hp"]))
            if numeric_hp is not None:
                hp = numeric_hp.fraction
                strip_present = True
        values: dict = {"at": at, "hp": hp, "death_marker": marker, "dead": self.death(hp, marker),
                        "level": self.profile.read_level(image, "target_level", reader)}
        # Имя произвольное: не используем список названий мобов как основной детектор.
        if names and names[0][2] >= .9:
            if self.last_player_name != names[0][1]:
                self.last_player_name = None
            if self.last_monster_name != names[0][1]:
                self.last_monster_name = None
            values.update(name=names[0][1], confidence=names[0][2])
            configured = {name.casefold(): kind for name, kind in self.profile.target_kind_names.items()}
            known_kind = configured.get(names[0][1].casefold())
            if known_kind is not None:
                values["kind"] = known_kind
            elif len(names) == 1 and (len(numbers) == 1 or marker or strip_present is True):
                # По подтверждённому пользователем UI одна строка с полосой HP — монстр.
                values["kind"] = "monster"
                if self.last_player_name == names[0][1]:
                    values["kind"] = "unknown"
            elif (len(names) == 1 and strip_present is False
                  and names[0][1] not in {self.last_player_name, self.last_monster_name}):
                values["kind"] = "npc"
            elif (len(names) in {2, 3}
                  and names[1][0] - names[0][0] > 5
                  and not any(char.isdigit() for char in names[1][1])):
                values.update(kind="player", player_class=names[1][1],
                              clan=names[2][1] if len(names) == 3 else None,
                              confidence=min(names[0][2], names[1][2]))
                self.last_player_name = names[0][1]
            if values.get("kind") == "monster":
                self.last_monster_name = names[0][1]
        result = TargetObservation(**values)
        self.cached_signature, self.cached = signature, result
        self.cached_config = configuration
        return result

    @staticmethod
    def death(hp: float | None, marker: bool) -> bool | None:
        # Читаемые ненулевые HP приоритетнее совпадения красного портрета.
        return hp == 0 if hp is not None else (True if marker else None)
