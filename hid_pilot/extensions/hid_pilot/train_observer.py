"""
Быстрые полосы пати независимо от медленного OCR собственных чисел.
"""

import logging
import threading
import time
from collections.abc import Callable

import numpy as np

from hid_pilot.bars import BarProfile, bar_fraction
from hid_pilot.desktop import Desktop, FocusLost
from hid_pilot.healer_observer import HealerObserver, HealerReader
from hid_pilot.paralysis import ParalysisDetector
from hid_pilot.party import PartyProfile
from hid_pilot.train_healer import TrainSnapshot

logger = logging.getLogger("hid_pilot.train.observer")


class TrainReader(HealerReader):
    def __init__(self, own: BarProfile, party: PartyProfile, detector: ParalysisDetector) -> None:
        super().__init__(own, party)
        self.detector = detector
        self.own_lock = threading.Lock()
        self.own_at = float("-inf")
        self.own_values: dict[str, float | None] = {}

    def update_own(self, image: np.ndarray, at: float) -> None:
        super().update_own(image, at)
        with self.profile_lock:
            values = {kind: self.own_cache.get(kind, (at, None))[1] for kind in ("hp", "mp", "cp")}
        with self.own_lock:
            self.own_values = values
            self.own_at = at

    def resource(self, image: np.ndarray, name: str) -> float | None:
        if name in self.own.bars:
            value = bar_fraction(self.own.crop(image, self.own.bars[name]), name)
            if value is not None:
                return value
        with self.own_lock:
            if time.monotonic() - self.own_at <= .75:
                return self.own_values.get(name)
        return None

    def read(self, image: np.ndarray, at: float) -> TrainSnapshot:
        with self.profile_lock:
            state = super().read(image, at)
            slots = tuple(member.slot for member in state.members)
            paralyzed = self.detector.read(image, slots)
            return TrainSnapshot(**state.model_dump(), paralyzed_slots=paralyzed, debuffs_known=True)


class TrainObserver(HealerObserver):
    def __init__(self, desktop: Desktop, reader: TrainReader, interval: Callable[[], float],
                 paused: Callable[[], bool]) -> None:
        super().__init__(desktop, reader, .1, paused)
        self.get_interval = interval
        self.ocr_worker = threading.Thread(target=self._own_loop, name="TrainOwnOCR", daemon=True)

    def start(self) -> None:
        # Даже загрузка OCR-модели не задерживает первый кадр пати.
        self.worker.start()
        self.ocr_worker.start()
        self.start_identity()

    def _own_loop(self) -> None:
        super()._own_loop()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                if self.paused():
                    self.stop_event.wait(.05)
                    continue
                at = time.monotonic()
                image = self.desktop.capture()
                with self.lock:
                    self.identity_image = image
                    self.identity_at = at
                state = self.reader.read(image, at)
                with self.lock:
                    self.samples.append(state)
                self.stop_event.wait(max(0, self.get_interval() - (time.monotonic() - at)))
            except FocusLost:
                self.stop_event.wait(.05)
            except Exception as exc:
                logger.exception("Наблюдение парика остановлено")
                with self.lock:
                    self.error = exc
                return

    def close(self) -> None:
        super().close()
        if self.ocr_worker.is_alive():
            self.ocr_worker.join(timeout=3)
