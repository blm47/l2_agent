"""
Непрерывное наблюдение хилера: полосы каждый tick, OCR только при изменении текста.
"""

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

import numpy as np

from hid_pilot.bars import BarProfile, bar_fraction, text_mask
from hid_pilot.desktop import Desktop, FocusLost
from hid_pilot.healer import HealerSnapshot
from hid_pilot.numbers import shared_numbers
from hid_pilot.paralysis import ParalysisDetector, ParalysisProfile
from hid_pilot.party import PartyMember, PartyProfile
from hid_pilot.player_target import PlayerIdentityReader
from hid_pilot.session_perception import SessionPerception

logger = logging.getLogger("hid_pilot.healer.observer")


class HealerReader:
    def __init__(self, own: BarProfile, party: PartyProfile) -> None:
        if (own.width, own.height) != (party.width, party.height):
            raise ValueError("Профили персонажа и пати имеют разные размеры")
        if not {"hp", "mp", "cp"} <= own.numbers.keys() | own.bars.keys():
            raise ValueError("Выполните автопоиск собственных HP/MP/CP")
        self.own = own
        self.party = party
        self.profile_lock = threading.RLock()
        self.revision = 0
        self.background_resources = False
        self.own_cache: dict[str, tuple[float, float]] = {}
        self.recovery = SessionPerception(own, party)
        self.resources: dict[str, tuple[bytes, float | None]] = {}
        self.identities = PlayerIdentityReader(own, party)
        reference = Path(__file__).resolve().parents[1] / "assets" / "destroyer.png"
        self.destroyer = ParalysisDetector(ParalysisProfile(width=party.width, height=party.height,
                                                         template=str(reference)), party)

    def resource(self, image: np.ndarray, name: str) -> float | None:
        if name not in self.own.numbers:
            return self.own.read(image, name)
        signature = text_mask(self.own.crop(image, self.own.numbers[name])).tobytes()
        cached = self.resources.get(name)
        if cached is not None and cached[0] == signature and cached[1] is not None:
            return cached[1]
        value, source = self.own.read_detail(image, name)
        # Цветовой fallback не кешируем: полоса может меняться без читаемых цифр.
        if source.startswith("OCR "):
            self.resources[name] = signature, value
        return value

    def read(self, image: np.ndarray, at: float) -> HealerSnapshot:
        with self.profile_lock:
            state = self._read(image, at)
            self.recovery.observe(state)
            return state.model_copy(update={"profile_revision": self.revision})

    def buffered_resource(self, image: np.ndarray, name: str, at: float) -> float | None:
        value = (bar_fraction(self.own.crop(image, self.own.bars[name]), name) if name in self.own.bars else None
                 ) if self.background_resources else self.resource(image, name)
        if value is not None:
            self.own_cache[name] = at, value
            return value
        cached = self.own_cache.get(name)
        return cached[1] if cached is not None and 0 <= at - cached[0] <= 1 else None

    def update_own(self, image: np.ndarray, at: float) -> None:
        with self.profile_lock:
            revision = self.revision
        values = {kind: HealerReader.resource(self, image, kind) for kind in ("hp", "mp", "cp")}
        with self.profile_lock:
            if revision == self.revision:
                self.own_cache.update({kind: (at, value) for kind, value in values.items()
                                       if value is not None and at >= self.own_cache.get(kind, (float("-inf"), 0))[0]})

    def auto_refresh(self, image: np.ndarray) -> None:
        party, own = self.recovery.find(image, self.own, time.monotonic())
        if party is None and own is None:
            return
        with self.profile_lock:
            if party is not None:
                self.party = party
                self.identities.party = party
                self.destroyer.party = party
                if hasattr(self, "detector"):
                    self.detector.party = party
            if own is not None:
                self.own = own
                self.identities.own = own
                self.resources.clear()
            self.revision += 1
            self.identities.invalidate()
        logger.info("Фоновая разметка обновлена, revision=%s; пати=%s, собственные статы=%s", self.revision, party is not None, own is not None)

    def _read(self, image: np.ndarray, at: float) -> HealerSnapshot:
        members = []
        known = True
        for slot in range(1, 9):
            values = {kind: bar_fraction(self.own.crop(image, self.party.box(kind, slot - 1)), kind)
                      for kind in ("cp", "hp", "mp")}
            if not any(value is not None for value in values.values()):
                continue
            if values["hp"] is None or values["mp"] is None:
                known = False
            members.append(PartyMember(slot=slot, **values))
        members, selected = self.identities.apply(image, members, at)
        destroyer_slots = self.destroyer.read(image, tuple(member.slot for member in members))
        return HealerSnapshot(at=at, hp=self.buffered_resource(image, "hp", at), mp=self.buffered_resource(image, "mp", at),
                              cp=self.buffered_resource(image, "cp", at), members=tuple(members), roster_known=known,
                              destroyer_slots=destroyer_slots,
                              **selected)


class HealerObserver:
    def __init__(self, desktop: Desktop, reader: HealerReader, interval: float,
                 paused: Callable[[], bool]) -> None:
        self.desktop = desktop
        self.reader = reader
        self.reader.background_resources = True
        self.interval = interval
        self.paused = paused
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.samples: deque[HealerSnapshot] = deque(maxlen=32)
        self.error: Exception | None = None
        self.worker = threading.Thread(target=self._run, name="HealerObserve", daemon=True)
        self.identity_image: np.ndarray | None = None
        self.identity_at = float("-inf")
        self.identity_worker = threading.Thread(target=self._identity_loop, name="PlayerTargetOCR", daemon=True)
        self.own_worker = threading.Thread(target=self._own_loop, name="HealerOwnOCR", daemon=True)

    def start(self) -> None:
        shared_numbers().prepare()
        self.worker.start()
        self.own_worker.start()
        self.start_identity()

    def start_identity(self) -> None:
        self.identity_worker.start()

    def _identity_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                with self.lock:
                    image = self.identity_image
                if image is not None and not self.paused():
                    self.reader.auto_refresh(image)
                    self.reader.identities.update(image)
            except Exception:
                logger.warning("Фоновый OCR временно недоступен; повтор через 0,5 сек", exc_info=True)
            self.stop_event.wait(.5)

    def _own_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                if not self.paused():
                    shared_numbers().prepare()
                    with shared_numbers().lock:
                        with self.lock:
                            image, at = self.identity_image, self.identity_at
                        if image is not None and 0 <= time.monotonic() - at <= 1:
                            self.reader.update_own(image, at)
            except FocusLost:
                pass
            except Exception:
                logger.warning("Собственный OCR временно недоступен; перепоиск будет повторён", exc_info=True)
            self.stop_event.wait(.3)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                if self.paused():
                    self.stop_event.wait(.05)
                    continue
                # Быстрые полосы не ждут фоновый OCR собственных чисел и перепоиск.
                started = time.monotonic()
                image = self.desktop.capture()
                with self.lock:
                    self.identity_image = image
                    self.identity_at = started
                state = self.reader.read(image, started)
                with self.lock:
                    self.samples.append(state)
                self.stop_event.wait(max(0, self.interval - (time.monotonic() - started)))
            except Exception as exc:
                if isinstance(exc, FocusLost) or self.paused() or self.stop_event.is_set():
                    self.stop_event.wait(.05)
                    continue
                logger.exception("Наблюдение хилера остановлено")
                with self.lock:
                    self.error = exc
                return

    def snapshots(self) -> tuple[HealerSnapshot, ...]:
        with self.lock:
            if self.error is not None:
                raise RuntimeError(f"Наблюдение хилера недоступно: {self.error}") from self.error
            return tuple(self.samples)

    def close(self) -> None:
        self.stop_event.set()
        if self.worker.is_alive():
            self.worker.join(timeout=3)
        if self.identity_worker.is_alive():
            self.identity_worker.join(timeout=3)
        if self.own_worker.is_alive():
            self.own_worker.join(timeout=3)
