"""
Распознавание трёх строк панели игрока и свежих имён пати.
"""

import threading
import time

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
        self.buffered_target: dict[str, object] = {}
        self.target_seen_at: float | None = None
        self.generation = 0
        self.party_signature: tuple[bytes, ...] | None = None
        self.names_checked_at = float("-inf")

    def invalidate(self) -> None:
        # После собственного выделения другой цели старый буфер уже не подтверждает таргет.
        with self.lock:
            self.generation += 1
            self.target = None
            self.buffered_target.clear()
            self.target_seen_at = None

    def signature(self, image: np.ndarray, slot: int | None = None) -> bytes:
        box = self.party.box("name", slot - 1) if slot is not None else self.own.target_player_panel
        return text_mask(self.own.crop(image, box)).tobytes() if box is not None else b""

    def update(self, image: np.ndarray) -> None:
        with self.lock:
            generation = self.generation
            previous_signature = self.party_signature
            previous_names = self.names.copy()
            previous_target = self.target
            checked_at = self.names_checked_at
        reader = shared_numbers()
        signature = tuple(self.signature(image, slot) for slot in range(1, 9))
        now = time.monotonic()
        confident = all(name is not None and confidence >= .9 for _, name, confidence in previous_names.values())
        reuse_names = signature == previous_signature and (confident or now - checked_at < 2)
        if reuse_names:
            names = previous_names
        else:
            members = read_party(image, self.party, reader)
            names = {member.slot: (signature[member.slot - 1], member.name, member.confidence)
                     for member in members}
        target_signature = self.signature(image)
        if previous_target is not None and previous_target[0] == target_signature and previous_target[1]:
            with self.lock:
                self.names = names
                self.party_signature = signature
                if not reuse_names:
                    self.names_checked_at = now
            return
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
            self.party_signature = signature
            if not reuse_names:
                self.names_checked_at = now
            if generation == self.generation:
                self.target = target_signature, values

    def apply(self, image: np.ndarray, members: list[PartyMember], at: float | None = None
              ) -> tuple[list[PartyMember], dict[str, object]]:
        now = time.monotonic() if at is None else at
        with self.lock:
            names, target = self.names.copy(), self.target
        result = []
        for member in members:
            cached = names.get(member.slot)
            if cached is not None and cached[0] == self.signature(image, member.slot):
                member = member.model_copy(update={"name": cached[1], "confidence": cached[2]})
            result.append(member)
        selected = target[1] if target is not None and target[0] == self.signature(image) else {}
        with self.lock:
            if selected:
                self.buffered_target = selected.copy()
                self.target_seen_at = now
            elif self.target_seen_at is not None and 0 <= now - self.target_seen_at <= 3:
                selected = self.buffered_target.copy()
            else:
                self.buffered_target.clear()
                self.target_seen_at = None
        return result, selected
