"""
Фоновый перепоиск только внутри подтверждённых областей текущей сессии.
"""

import logging

import numpy as np

from hid_pilot.auto_numbers import find_numbers
from hid_pilot.auto_party import find_party
from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.healer import HealerSnapshot
from hid_pilot.numbers import shared_numbers
from hid_pilot.party import PartyProfile

logger = logging.getLogger("hid_pilot.healer.recovery")


def region(boxes: list[BarBox], padding: float = .02) -> BarBox:
    return BarBox(x1=max(0, min(box.x1 for box in boxes) - padding),
                  y1=max(0, min(box.y1 for box in boxes) - padding),
                  x2=min(1, max(box.x2 for box in boxes) + padding),
                  y2=min(1, max(box.y2 for box in boxes) + padding))


class SessionPerception:
    def __init__(self, own: BarProfile, party: PartyProfile) -> None:
        self.party_region = region([party.box(kind, slot) for slot in range(8) for kind in ("name", "cp", "hp", "mp")])
        self.first_cp_y = party.cp.y1
        self.first_cp_tolerance = max(3 / party.height, party.cp.y2 - party.cp.y1)
        self.own_region = region([box for boxes in (own.numbers, own.bars) for kind, box in boxes.items()
                                  if kind in {"cp", "hp", "mp"}], .04)
        self.signature: tuple | None = None
        self.latest_signature: tuple | None = None
        self.changed_since: float | None = None
        self.party_pending = False
        self.own_pending = False
        self.last_attempt = float("-inf")

    def observe(self, state: HealerSnapshot) -> None:
        known = dict(self.signature or ())
        signature = tuple((member.slot, member.name or known.get(member.slot)) for member in state.members)
        if self.signature is not None and tuple(slot for slot, _ in signature) == tuple(slot for slot, _ in self.signature):
            # Первое чтение имени и временный пропуск OCR не означают смену участника.
            self.signature = tuple((slot, name or dict(signature).get(slot)) for slot, name in self.signature)
        self.latest_signature = signature
        if self.signature is None:
            self.signature = signature
        elif signature != self.signature:
            if self.changed_since is None:
                self.changed_since = state.at
            if state.at - self.changed_since >= .5:
                self.party_pending = True
        else:
            self.changed_since = None
        if any(member.hp is None for member in state.members):
            self.party_pending = True
        if not state.members:
            self.party_pending = True
        if state.hp is None or state.mp is None or state.cp is None:
            self.own_pending = True

    def find(self, image: np.ndarray, own: BarProfile, now: float) -> tuple[PartyProfile | None, BarProfile | None]:
        if now - self.last_attempt < 2 or not (self.party_pending or self.own_pending):
            return None, None
        self.last_attempt = now
        party, updated = None, None
        if self.party_pending:
            logger.info("Изменение/потеря пати: фоновый OCR в подтверждённой области")
            candidates = find_party(image, shared_numbers(), self.party_region)
            if len(candidates) == 1:
                candidate = candidates[0].profile
                if candidate.cp.y1 > self.first_cp_y + self.first_cp_tolerance:
                    logger.warning("Фоновая разметка пропускает первую строку пати; старая разметка сохранена")
                    return None, None
                boxes = [candidate.box(kind, slot) for slot in range(8) for kind in ("name", "cp", "hp", "mp")]
                if all(self.party_region.x1 <= box.x1 < box.x2 <= self.party_region.x2
                       and self.party_region.y1 <= box.y1 < box.y2 <= self.party_region.y2 for box in boxes):
                    party = candidate
                    self.party_pending = False
                    self.signature = self.latest_signature
                    self.changed_since = None
        if self.own_pending:
            logger.info("Собственные CP/HP/MP потеряны: фоновый OCR подтверждённой области")
            box = self.own_region
            height, width = image.shape[:2]
            x, y = round(box.x1 * width), round(box.y1 * height)
            crop = image[y:round(box.y2 * height), x:round(box.x2 * width)]
            candidates = find_numbers(crop, shared_numbers(), False)
            numbers = {}
            for kind in ("cp", "hp", "mp"):
                choices = [candidate for candidate in candidates if candidate.kind == kind]
                if len(choices) == 1:
                    found = choices[0].box
                    numbers[kind] = BarBox(x1=(x + found.x1 * crop.shape[1]) / width,
                                          x2=(x + found.x2 * crop.shape[1]) / width,
                                          y1=(y + found.y1 * crop.shape[0]) / height,
                                          y2=(y + found.y2 * crop.shape[0]) / height)
            if len(numbers) == 3:
                updated = own.model_copy(update={"numbers": numbers})
                self.own_pending = False
        return party, updated
