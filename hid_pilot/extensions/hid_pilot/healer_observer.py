"""
Непрерывное наблюдение хилера: полосы каждый tick, OCR только при изменении текста.
"""

import logging
import threading
import time
from collections import deque
from collections.abc import Callable

import numpy as np

from hid_pilot.bars import BarProfile, bar_fraction, text_mask
from hid_pilot.desktop import Desktop, FocusLost
from hid_pilot.healer import HealerSnapshot
from hid_pilot.numbers import shared_numbers
from hid_pilot.party import PartyMember, PartyProfile
from hid_pilot.player_target import PlayerIdentityReader

logger = logging.getLogger("hid_pilot.healer.observer")


class HealerReader:
    def __init__(self, own: BarProfile, party: PartyProfile) -> None:
        if (own.width, own.height) != (party.width, party.height):
            raise ValueError("Профили персонажа и пати имеют разные размеры")
        if not {"hp", "mp", "cp"} <= own.numbers.keys() | own.bars.keys():
            raise ValueError("Выполните автопоиск собственных HP/MP/CP")
        self.own = own
        self.party = party
        self.resources: dict[str, tuple[bytes, float | None]] = {}
        self.identities = PlayerIdentityReader(own, party)

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
        members, selected = self.identities.apply(image, members)
        return HealerSnapshot(at=at, hp=self.resource(image, "hp"), mp=self.resource(image, "mp"),
                              cp=self.resource(image, "cp"), members=tuple(members), roster_known=known,
                              **selected)


class HealerObserver:
    def __init__(self, desktop: Desktop, reader: HealerReader, interval: float,
                 paused: Callable[[], bool]) -> None:
        self.desktop = desktop
        self.reader = reader
        self.interval = interval
        self.paused = paused
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.samples: deque[HealerSnapshot] = deque(maxlen=32)
        self.error: Exception | None = None
        self.worker = threading.Thread(target=self._run, name="HealerObserve", daemon=True)
        self.identity_image: np.ndarray | None = None
        self.identity_worker = threading.Thread(target=self._identity_loop, name="PlayerTargetOCR", daemon=True)

    def start(self) -> None:
        shared_numbers().prepare()
        self.worker.start()
        self.start_identity()

    def start_identity(self) -> None:
        self.identity_worker.start()

    def _identity_loop(self) -> None:
        try:
            while not self.stop_event.is_set():
                with self.lock:
                    image = self.identity_image
                if image is not None and not self.paused():
                    self.reader.identities.update(image)
                self.stop_event.wait(.5)
        except Exception:
            logger.warning("OCR выбранного игрока недоступен; повторное выделение остаётся включено", exc_info=True)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                if self.paused():
                    self.stop_event.wait(.05)
                    continue
                # Ожидание общего OCR происходит до capture и не старит новый кадр.
                with shared_numbers().lock:
                    started = time.monotonic()
                    image = self.desktop.capture()
                    with self.lock:
                        self.identity_image = image
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
