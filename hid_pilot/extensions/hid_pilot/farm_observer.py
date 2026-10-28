"""
Фоновый OCR имени, уровней и пати с одним последним кадром и ограниченным кешем.
"""

import logging
import threading
import time

import numpy as np

from hid_pilot.bars import BarProfile
from hid_pilot.monster_catalogue import MonsterCatalogue, MonsterNameConsensus
from hid_pilot.numbers import NumbersReader
from hid_pilot.party import PartyMember, PartyProfile, read_party, read_party_bars
from hid_pilot.target import TargetObservation, TargetReader, target_text_signature
from hid_pilot.system_chat import ChatMessage, SystemChatOCR
from hid_pilot.party_class import PartyClassDetector

logger = logging.getLogger("hid_pilot.farm.observer")
_background_reader = NumbersReader()


def background_numbers() -> NumbersReader:
    # Отдельный от быстрых полос engine загружается до ARM и повторно используется между запусками.
    return _background_reader


class FarmIdentityCache:
    def __init__(self, profile: BarProfile, party: PartyProfile | None,
                 catalogue: MonsterCatalogue | None = None) -> None:
        self.profile = profile
        self.party = party
        self.class_detector = PartyClassDetector(party) if party is not None else None
        # Отдельный OCR не блокирует быстрые HP/MP общим lock.
        self.numbers = background_numbers()
        self.reader = TargetReader(profile)
        # Быстрое HP уже читает основной поток; фон не дублирует числовой OCR.
        self.reader.identity_only = True
        self.consensus = MonsterNameConsensus(catalogue) if catalogue is not None else None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.pending: tuple | None = None
        self.result: tuple | None = None
        self.identity_result: tuple | None = None
        self.own_signature: bytes | None = None
        self.own_level_result: tuple[bytes, int] | None = None
        self.level_signature: bytes | None = None
        self.party_at: float | None = None
        self.party_result: tuple[list[PartyMember], dict[int, bytes]] | None = None
        self.epoch = 0
        self.chat = SystemChatOCR(profile, self.numbers)
        self.submitted = float("-inf")
        self.worker = threading.Thread(target=self._run, name="FarmIdentityOCR", daemon=True)
        self.worker.start()

    def signature(self, image: np.ndarray) -> bytes:
        box = self.profile.target_panel or self.profile.target_player_panel
        if box is None:
            return b""
        crop = self.profile.crop(image, box).copy()
        height, width = image.shape[:2]
        left, top = round(box.x1 * width), round(box.y1 * height)
        # Изменение HP, уровня или черепа не меняет имя выбранной цели.
        for excluded in (self.profile.numbers.get("target_hp"), self.profile.bars.get("target_hp"),
                         self.profile.target_hp_presence_box, self.profile.target_dead_marker,
                         self.profile.target_level):
            if excluded is not None:
                x1, x2 = max(0, round(excluded.x1 * width) - left), min(crop.shape[1], round(excluded.x2 * width) - left)
                y1, y2 = max(0, round(excluded.y1 * height) - top), min(crop.shape[0], round(excluded.y2 * height) - top)
                if x2 > x1 and y2 > y1:
                    crop[y1:y2, x1:x2] = 0
        return target_text_signature(crop)

    def invalidate(self) -> None:
        with self.lock:
            self.epoch += 1
            self.result = None
            self.identity_result = None
            self.own_signature = None
            self.level_signature = None
            self.pending = None
            self.submitted = float("-inf")
            if self.consensus is not None:
                self.consensus.reset_target()

    def snapshot(self, image: np.ndarray, at: float) -> tuple[TargetObservation, int | None, list[PartyMember]]:
        signature = self.signature(image)
        now = time.monotonic()
        with self.lock:
            if now - self.submitted >= .5:
                self.pending = (image.copy(), now, signature, self.epoch)
                self.submitted = now
            result = self.result
            epoch = self.epoch
            own_signature = self.own_signature
            level_signature = self.level_signature
            own_result = self.own_level_result
            party_result = self.party_result
        members = []
        if self.party is not None:
            metadata = []
            if party_result is not None:
                stored_members, signatures = party_result
                for member in stored_members:
                    box = self.party.box("name", member.slot - 1)
                    if target_text_signature(self.profile.crop(image, box)) == signatures.get(member.slot):
                        metadata.append(member)
            # Ресурсы берутся только из текущего кадра, а не из старого результата OCR.
            members = read_party_bars(image, self.party, metadata)
        own_current = (target_text_signature(self.profile.crop(image, self.profile.own_level))
                       if self.profile.own_level is not None else None)
        cached_level = own_result[1] if own_result is not None and own_result[0] == own_current else None
        if result is None or result[3] != epoch or not 0 <= now - result[0] <= 2:
            return TargetObservation(at=at, epoch=epoch), cached_level, members
        _, stored_signature, observation, _, level, _ = result
        if own_signature is not None and self.profile.own_level is not None:
            if own_current != own_signature:
                level = None
        if level is None:
            level = cached_level
        if level_signature is not None and self.profile.target_level is not None:
            if target_text_signature(self.profile.crop(image, self.profile.target_level)) != level_signature:
                observation = observation.model_copy(update={"level": None})
        if stored_signature != signature:
            observation = TargetObservation(at=at, epoch=epoch)
        else:
            # Переносим только идентичность; старое HP никогда не становится свежим.
            observation = observation.model_copy(update={"at": at, "hp": None, "dead": None,
                                                         "death_marker": False, "epoch": epoch})
        return observation, level, members

    def _run(self) -> None:
        while not self.stop_event.wait(.05):
            with self.lock:
                pending, self.pending = self.pending, None
            if pending is None:
                continue
            image, at, signature, epoch = pending
            try:
                self.read_chat(image, at)
                cached = self.identity_result
                reuse = (cached is not None and cached[0] == signature and cached[1] == epoch
                         and (self.consensus is None or cached[2].name
                              and cached[2].name_confirmations >= 2))
                if reuse:
                    observation = cached[2].model_copy(update={"at": at, "hp": None, "dead": None,
                                                              "death_marker": False})
                    observation = observation.model_copy(update={"level": self.profile.read_level(
                        image, "target_level", self.numbers)})
                elif self.profile.target_panel or self.profile.target_player_panel:
                    # Каждый голос — реальный запуск OCR по отдельному свежему кадру.
                    self.reader.cached_signature = None
                    observation = self.reader.read(image, at, self.numbers)
                else:
                    observation = TargetObservation(at=at, level=self.profile.read_level(
                        image, "target_level", self.numbers))
                # Идентичность проверяется подписью текущего кадра; HP из кеша не переносится.
                level_signature = (target_text_signature(self.profile.crop(image, self.profile.target_level))
                                   if self.profile.target_level is not None else None)
                with self.lock:
                    if epoch != self.epoch:
                        continue
                    # Свежий ожидающий кадр с той же подписью подтверждает неизменность имени.
                    latest = self.pending
                    if (latest is not None and latest[2] == signature and latest[3] == epoch
                            and 0 <= time.monotonic() - latest[1] <= 2):
                        observation = observation.model_copy(update={"at": latest[1]})
                    if self.consensus is not None and not reuse:
                        observation = self.consensus.observe(observation, time.monotonic())
                    self.identity_result = (signature, epoch, observation)
                    self.level_signature = level_signature
                    self.result = (time.monotonic(), signature, observation, epoch, None, [])
                level = self.profile.read_level(image, "own_level", self.numbers)
                with self.lock:
                    if epoch != self.epoch:
                        continue
                    self.own_signature = (target_text_signature(self.profile.crop(image, self.profile.own_level))
                                          if self.profile.own_level is not None else None)
                    if self.own_signature is not None and level is not None:
                        self.own_level_result = (self.own_signature, level)
                    self.result = (time.monotonic(), signature, observation, epoch, level, [])
                members = read_party(image, self.party, self.numbers) if self.party is not None else []
                if self.class_detector is not None:
                    members = self.class_detector.apply(image, members, at)
                party_signatures = {
                    member.slot: target_text_signature(self.profile.crop(
                        image, self.party.box("name", member.slot - 1)))
                    for member in members} if self.party is not None else {}
                with self.lock:
                    if self.party is not None:
                        if self.party_result is not None:
                            old_members, old_signatures = self.party_result
                            old = {member.slot: member for member in old_members}
                            refreshed = []
                            for member in members:
                                previous = old.get(member.slot)
                                if (previous is not None and previous.name
                                        and old_signatures.get(member.slot) == party_signatures.get(member.slot)
                                        and (not member.name or previous.confidence > member.confidence)):
                                    member = member.model_copy(update={"name": previous.name,
                                                                        "confidence": previous.confidence})
                                refreshed.append(member)
                            members = refreshed
                        self.party_result = (members, party_signatures)
                        self.party_at = at
                    if epoch == self.epoch:
                        # Публикация пати не обновляет время уже прочитанной идентичности.
                        published = self.result[0]
                        self.result = (published, signature, observation, epoch, level, members)
            except Exception:
                logger.warning("Фоновый OCR фарма временно недоступен", exc_info=True)

    def reset_chat(self) -> None:
        self.chat.reset_chat()

    def chat_ready(self) -> bool:
        return self.chat.chat_ready()

    def take_chat_events(self) -> list[ChatMessage]:
        return self.chat.take_chat_events()

    def read_chat(self, image: np.ndarray, at: float) -> None:
        self.chat.numbers = self.numbers
        self.chat.read_chat(image, at)

    def close(self) -> None:
        self.chat.close()
        self.stop_event.set()
        self.worker.join(timeout=2)
