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
from hid_pilot.party import PartyMember, PartyProfile, read_party
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
        self.consensus = MonsterNameConsensus(catalogue) if catalogue is not None else None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.pending: tuple | None = None
        self.result: tuple | None = None
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
            self.pending = None
            self.submitted = float("-inf")

    def snapshot(self, image: np.ndarray, at: float) -> tuple[TargetObservation, int | None, list[PartyMember]]:
        signature = self.signature(image)
        now = time.monotonic()
        with self.lock:
            if now - self.submitted >= .5:
                self.pending = (image.copy(), now, signature, self.epoch)
                self.submitted = now
            result = self.result
            epoch = self.epoch
        if result is None or result[3] != epoch or not 0 <= now - result[0] <= 2:
            return TargetObservation(at=at, epoch=epoch), None, []
        _, stored_signature, observation, _, level, members = result
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
                if self.profile.target_panel or self.profile.target_player_panel:
                    # Каждый голос — реальный запуск OCR по отдельному свежему кадру.
                    self.reader.cached_signature = None
                    observation = self.reader.read(image, at, self.numbers)
                else:
                    observation = TargetObservation(at=at, level=self.profile.read_level(
                        image, "target_level", self.numbers))
                level = self.profile.read_level(image, "own_level", self.numbers)
                members = read_party(image, self.party, self.numbers) if self.party is not None else []
                if self.class_detector is not None:
                    members = self.class_detector.apply(image, members, at)
                with self.lock:
                    if epoch == self.epoch:
                        if self.consensus is not None:
                            observation = self.consensus.observe(observation, time.monotonic())
                        self.result = (at, signature, observation, epoch, level, members)
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
