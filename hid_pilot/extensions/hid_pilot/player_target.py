"""
Распознавание трёх строк панели игрока и свежих имён пати.
"""

import threading

import numpy as np

from hid_pilot.bars import BarProfile, text_mask
from hid_pilot.numbers import shared_numbers
from hid_pilot.party import PartyMember, PartyProfile, read_party


class PlayerIdentityReader:
    def __init__(self, own: BarProfile, party: PartyProfile) -> None:
        self.own = own
        self.party = party
        self.lock = threading.Lock()
        self.names: dict[int, tuple[bytes, str | None, float]] = {}
        self.target: tuple[bytes, dict[str, object]] | None = None

    def signature(self, image: np.ndarray, slot: int | None = None) -> bytes:
        box = self.party.box("name", slot - 1) if slot is not None else self.own.target_player_panel
        return text_mask(self.own.crop(image, box)).tobytes() if box is not None else b""

    def update(self, image: np.ndarray) -> None:
        reader = shared_numbers()
        members = read_party(image, self.party, reader)
        names = {member.slot: (self.signature(image, member.slot), member.name, member.confidence)
                 for member in members}
        crop = self.own.crop(image, self.own.target_player_panel) if self.own.target_player_panel is not None else None
        rows = sorted([(float(np.mean(np.asarray(points)[:, 1])), text.strip(), score)
                       for points, text, score in (reader.detect(crop) if crop is not None else [])
                       if score >= .85 and text.strip()], key=lambda row: row[0])
        values: dict[str, object] = {}
        # Ник и класс обязательны; клан может отсутствовать. Не угадываем пропущенные строки.
        if (len(rows) in {2, 3} and rows[0][2] >= .9
                and not any(char.isdigit() for char in rows[1][1])
                and all("/" not in row[1] for row in rows)):
            values = {"selected_name": rows[0][1], "selected_class": rows[1][1],
                      "selected_clan": rows[2][1] if len(rows) == 3 else None,
                      "selected_confidence": min(rows[0][2], rows[1][2])}
        with self.lock:
            self.names = names
            self.target = self.signature(image), values

    def apply(self, image: np.ndarray, members: list[PartyMember]) -> tuple[list[PartyMember], dict[str, object]]:
        with self.lock:
            names, target = self.names.copy(), self.target
        result = []
        for member in members:
            cached = names.get(member.slot)
            if cached is not None and cached[0] == self.signature(image, member.slot):
                member = member.model_copy(update={"name": cached[1], "confidence": cached[2]})
            result.append(member)
        selected = target[1] if target is not None and target[0] == self.signature(image) else {}
        return result, selected
