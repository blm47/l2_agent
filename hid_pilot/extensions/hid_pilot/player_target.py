"""
Распознавание трёх строк панели игрока и свежих имён пати.
"""

import threading
import time

import numpy as np

from hid_pilot.bars import BarProfile, text_mask
from hid_pilot.numbers import shared_numbers
from hid_pilot.party import PartyMember, PartyProfile, read_party
from hid_pilot.target import TargetObservation, TargetReader


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
        self.target_reader = TargetReader(own)

    def invalidate(self) -> None:
        # После собственного выделения другой цели старый буфер уже не подтверждает таргет.
        with self.lock:
            self.generation += 1
            self.target = None
            self.buffered_target.clear()
            self.target_seen_at = None
            self.target_reader.cached = None
            self.target_reader.cached_signature = None
            self.target_reader.last_player_name = None
            self.target_reader.last_monster_name = None

    def signature(self, image: np.ndarray, slot: int | None = None) -> bytes:
        if slot is None:
            self.target_reader.profile = self.own
            return self.target_reader.signature(image)
        box = self.party.box("name", slot - 1)
        return text_mask(self.own.crop(image, box)).tobytes() if box is not None else b""

    def update(self, image: np.ndarray, at: float | None = None) -> TargetObservation:
        with self.lock:
            generation = self.generation
            previous_signature = self.party_signature
            previous_names = self.names.copy()
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
        self.target_reader.profile = self.own
        target_signature = self.signature(image)
        observation = self.target_reader.read(image, at, reader).model_copy(update={"epoch": generation})
        values: dict[str, object] = {}
        if observation.name:
            values = {"selected_name": observation.name, "selected_class": observation.player_class,
                      "selected_clan": observation.clan, "selected_confidence": observation.confidence,
                      "target": observation}
        with self.lock:
            self.names = names
            self.party_signature = signature
            if not reuse_names:
                self.names_checked_at = now
            if generation == self.generation:
                self.target = target_signature, values
                return observation
        return TargetObservation(at=observation.at)

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
        if self.own.target_panel is not None and not selected:
            with self.lock:
                self.buffered_target.clear()
                self.target_seen_at = None
            return result, {}
        with self.lock:
            if selected:
                self.buffered_target = selected.copy()
                self.target_seen_at = now
            elif self.target_seen_at is not None and 0 <= now - self.target_seen_at <= 3:
                selected = self.buffered_target.copy()
            else:
                self.buffered_target.clear()
                self.target_seen_at = None
        # Для статистики нельзя переносить старую личность на кадр новой цели.
        if "target" in selected:
            observation = selected["target"]
            if target is None or target[0] != self.signature(image) or not 0 <= now - observation.at <= 1:
                selected = {key: value for key, value in selected.items() if key != "target"}
        return result, selected
