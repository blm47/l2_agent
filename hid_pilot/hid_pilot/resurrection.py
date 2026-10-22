"""
Ожидание чужого resurrection и подтверждение только распознанного диалога.
"""

import logging
import re
import time
from collections.abc import Callable

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict

from hid_pilot.bars import BarProfile
from hid_pilot.controller import ActionController
from hid_pilot.desktop import FocusLost
from hid_pilot.numbers import shared_numbers

logger = logging.getLogger("hid_pilot.resurrection")


class ResurrectionOffer(BaseModel):
    model_config = ConfigDict(frozen=True)
    button: tuple[int, int, int, int]
    message: tuple[int, int, int, int]


def find_offer(image: np.ndarray) -> ResurrectionOffer | None:
    reader = shared_numbers()
    reader.prepare()
    scale = min(1., 1280 / image.shape[1])
    frame = cv2.resize(image, None, fx=scale, fy=scale) if scale < 1 else image
    with reader.lock:
        rows, _ = reader.engine(frame, use_det=True, use_cls=False)
    texts = []
    for points, text, confidence in rows or []:
        if float(confidence) < .9:
            continue
        points = np.asarray(points) / scale
        x1, y1 = np.floor(points.min(axis=0)).astype(int)
        x2, y2 = np.ceil(points.max(axis=0)).astype(int)
        if 0 <= x1 < x2 <= image.shape[1] and 0 <= y1 < y2 <= image.shape[0]:
            texts.append((re.sub(r"[^a-z]", "", str(text).lower()), (x1, y1, x2, y2)))
    offers = []
    for text, accept in texts:
        if text != "accept":
            continue
        ax, ay, bx, by = accept
        height = by - ay
        declines = [box for word, box in texts if word == "decline"
                    and bx < box[0] < bx + 12 * height and abs(box[1] - ay) < height]
        messages = [box for word, box in texts if "resurrection" in word
                    and ax - 12 * height < box[0] < bx + 6 * height
                    and ay - 12 * height < box[1] < ay - height]
        # Одна кнопка Accept без контекста не разрешает клик.
        if len(declines) == 1 and messages:
            offers.append(ResurrectionOffer(button=accept, message=messages[0]))
    return offers[0] if len(offers) == 1 else None


class OfferChanged(Exception):
    pass


def wait_for_resurrection(controller: ActionController, profile: BarProfile, deadline: float,
                          notify: Callable[[str], None],
                          clock: Callable[[], float] = time.monotonic) -> None:
    def report(text: str) -> None:
        logger.info(text)
        notify(text)

    report("Собственное HP = 0: ожидание resurrection; боевые действия приостановлены")
    death_reported = False
    clicked = False
    next_click = 0.
    while clock() < deadline:
        controller.checkpoint()
        generation = controller.pause_generation
        image = controller.capture().copy()
        started = clock()
        hp = profile.read(image, "hp")
        if clock() - started > 1 or generation != controller.pause_generation:
            controller.wait(.5)
            continue
        if hp is not None and hp > 0:
            revived = getattr(notify, "revived", None)
            if revived is not None:
                revived()
            report("Персонаж воскрес: HP восстановлено, сценарий продолжается")
            return
        if hp == 0 and not death_reported:
            death = getattr(notify, "death", None)
            if death is not None:
                death()
            death_reported = True
        offer = find_offer(image)
        controller.checkpoint()
        if offer is None:
            clicked = False
        elif (not clicked and clock() >= next_click and clock() - started <= 2
              and generation == controller.pause_generation):
            height, width = image.shape[:2]
            x1, y1, x2, y2 = offer.button

            def verify() -> None:
                if clock() >= deadline or generation != controller.pause_generation:
                    raise OfferChanged()
                # verify вызывается под lock контроллера: автопаузу здесь не ожидаем.
                try:
                    current = controller.desktop.capture()
                except FocusLost as exc:
                    raise OfferChanged() from exc
                if generation != controller.pause_generation or clock() >= deadline:
                    raise OfferChanged()
                if current.shape != image.shape:
                    raise OfferChanged()
                # Проверяем текст сообщения и кнопки на каждом шаге наведения.
                for left, top, right, bottom in (offer.message, offer.button):
                    old = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2GRAY) > 150
                    new = cv2.cvtColor(current[top:bottom, left:right], cv2.COLOR_BGR2GRAY) > 150
                    if np.mean(old != new) > .12:
                        raise OfferChanged()

            try:
                controller.click("left", (x1 + x2) / (2 * width), (y1 + y2) / (2 * height), verify=verify)
            except OfferChanged:
                logger.info("Диалог resurrection изменился при наведении; клик отменён")
            else:
                clicked = True
                next_click = clock() + 5
                report("Resurrection: нажата Accept; ожидается подтверждение по собственному HP")
        controller.wait(.5)
    raise RuntimeError("Собственное HP = 0: время сценария истекло в ожидании resurrection")
