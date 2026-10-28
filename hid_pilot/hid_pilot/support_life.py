"""
Подтверждение смерти саппорта по панели пати МА или собственному окну.
"""

import logging
import threading
import time
from collections.abc import Callable
from functools import lru_cache

import cv2
import numpy as np

from hid_pilot.auto_numbers import find_numbers
from hid_pilot.controller import ActionController
from hid_pilot.farm_observer import background_numbers
from hid_pilot.numbers import shared_numbers
from hid_pilot.extensions import extensions_dir

logger = logging.getLogger("hid_pilot.support_life")


def empty_hp_bar(image: np.ndarray) -> bool:
    """
    Отличает отсутствие заполнения HP от полосы с нераспознанным красным фрагментом.
    Наличие самой строки пати проверяется вызывающим кодом по соседним ресурсам.
    """
    height, width = image.shape[:2]
    if height < 3 or width < 20 or width / height < 4:
        return False
    hue, saturation, value = cv2.split(cv2.cvtColor(image, cv2.COLOR_BGR2HSV))
    red = ((hue <= 12) | (hue >= 165)) & (saturation >= 85) & (value >= 45)
    return not bool(np.any(red))


@lru_cache(maxsize=1)
def village_button_template() -> np.ndarray | None:
    path = extensions_dir() / "assets" / "to-village.png"
    if not path.is_file():
        logger.warning("Эталон кнопки To Village отсутствует; смерть проверяется по HP")
        return None
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


def village_button_visible(image: np.ndarray) -> bool:
    template = village_button_template()
    if template is None:
        return False
    height, width = image.shape[:2]
    center = image[round(height * .25):round(height * .7),
                   round(width * .25):round(width * .75)]
    if center.shape[0] < template.shape[0] or center.shape[1] < template.shape[1]:
        return False
    score = cv2.matchTemplate(cv2.cvtColor(center, cv2.COLOR_BGR2GRAY), template,
                             cv2.TM_CCOEFF_NORMED)
    return bool(np.isfinite(score).all() and float(score.max()) >= .95)


class SupportHPReader:
    def __init__(self) -> None:
        self.box: tuple[int, int, int, int] | None = None
        self.shape: tuple[int, ...] | None = None
        self.lock = threading.Lock()
        self.worker: threading.Thread | None = None
        self.calibrating = False

    @property
    def needs_calibration(self) -> bool:
        with self.lock:
            return self.box is None and not self.calibrating

    def prepare(self, image: np.ndarray) -> None:
        """
        Передаёт в фон только ограниченный HUD, без ожидания OCR в боевом потоке.
        """
        with self.lock:
            if self.shape != image.shape:
                self.shape = image.shape
                self.box = None
            if self.calibrating or self.box is not None:
                return
            self.calibrating = True
            self.worker = threading.Thread(target=self._locate, args=(image[:220, :700].copy(), image.shape),
                                           name="SupportHPROI", daemon=True)
            self.worker.start()

    def _locate(self, hud: np.ndarray, shape: tuple[int, ...]) -> None:
        try:
            # Этот OCR engine уже используется фоновым наблюдением; fast HP/MP не блокируем.
            reader = background_numbers()
            reader.prepare()
            candidates = [item for item in find_numbers(hud, reader, top_only=False)
                          if item.kind == "hp" and item.numbers.confidence >= .9]
            if len(candidates) != 1:
                return
            height, width = hud.shape[:2]
            box = candidates[0].box
            located = (round(box.x1 * width), round(box.y1 * height),
                       round(box.x2 * width), round(box.y2 * height))
            with self.lock:
                if self.shape == shape:
                    self.box = located
                    logger.info("Найдена числовая область собственного HP саппорта: %s", located)
        except Exception:
            logger.warning("Область собственного HP саппорта пока не распознана", exc_info=True)
        finally:
            with self.lock:
                self.calibrating = False

    def __call__(self, image: np.ndarray) -> float | None:
        """
        Находит HP по метке, затем читает только числовую ROI на новых кадрах.
        """
        with self.lock:
            box = self.box if self.shape == image.shape else None
        if box is not None:
            x1, y1, x2, y2 = box
            numbers = shared_numbers().read(image[y1:y2, x1:x2])
            if numbers is not None and numbers.confidence >= .9:
                return numbers.fraction
            with self.lock:
                self.box = None
        self.prepare(image)
        return None


class SupportLifeObserver:
    def __init__(self, death: Callable[[], None], revived: Callable[[], None],
                 reader: Callable[[np.ndarray], float | None] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 button_reader: Callable[[np.ndarray], bool] = village_button_visible) -> None:
        self.death = death
        self.revived = revived
        self.reader = reader if reader is not None else SupportHPReader()
        self.clock = clock
        self.button_reader = button_reader
        self.dead = False

    def observe_party_hp(self, hp: float | None) -> None:
        """
        Принимает свежее HP из видимой строки саппорта в окне МА.
        """
        if hp == 0:
            self._mark_dead("пустая HP-полоса саппорта в панели пати МА")
        elif hp is not None and hp > 0:
            self._alive()

    def observe(self, controller: ActionController, party_hp: float | None) -> None:
        """
        Вызывается после проверенного переключения в окно саппорта, до его макроса.
        """
        if party_hp is not None and party_hp > 0:
            if isinstance(self.reader, SupportHPReader) and self.reader.needs_calibration:
                self.reader.prepare(controller.capture())
            self._alive()
            return
        reading = self._read(controller)
        if reading is None:
            return
        hp, village = reading
        if hp is not None and hp > 0:
            if village:
                return
            self._alive()
            return
        if hp != 0 and not village:
            return
        if self.dead:
            return
        # Пустая полоса пати не смерть: подтверждаем HP=0 или кнопку на двух новых кадрах.
        generation = controller.pause_generation
        controller.wait(.15)
        confirmed = self._read(controller)
        if generation != controller.pause_generation or confirmed is None:
            return
        confirmed_hp, confirmed_village = confirmed
        if (confirmed_hp is not None and confirmed_hp > 0
                or not (hp == confirmed_hp == 0 or village and confirmed_village)):
            return
        self._mark_dead("HP=0 или кнопка To Village подтверждены на двух свежих кадрах")

    def _mark_dead(self, reason: str) -> None:
        if not self.dead:
            self.dead = True
            logger.info("Саппорт умер: %s", reason)
            self.death()

    def _read(self, controller: ActionController) -> tuple[float | None, bool] | None:
        generation = controller.pause_generation
        started = self.clock()
        image = controller.capture()
        hp = self.reader(image)
        village = self.button_reader(image)
        controller.checkpoint()
        if (self.clock() - started > 1 or generation != controller.pause_generation
                or hp is not None and not 0 <= hp <= 1):
            return None
        return hp, village

    def _alive(self) -> None:
        if self.dead:
            self.dead = False
            logger.info("Саппорт воскрес: подтверждено положительное HP")
            self.revived()
