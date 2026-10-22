"""
Консервативный разбор новых строк системного чата без игрового ввода.
"""

import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from hid_pilot.bars import BarProfile
from hid_pilot.numbers import NumbersReader
from hid_pilot.chat_preview import publish_chat_preview

logger = logging.getLogger("hid_pilot.chat_ocr")
chat_logger = logging.getLogger("hid_pilot.system_chat")
SYSTEM_CHAT_ENABLED = False


@dataclass(frozen=True)
class ChatMessage:
    kind: str
    text: str
    at: float
    amount: int | None = None
    sp: int | None = None


def parse_message(text: str, at: float) -> ChatMessage:
    normalized = " ".join(text.casefold().split()).rstrip(".! ")
    if normalized in {"recharge cannot be used due to unsuitable terms",
                      "rechargecannotbeused dduetounsuitable eterms",
                      "rechardge cannot be used to unsituable terns"}:
        return ChatMessage("mana_target_block", text, at)
    if normalized in {"you cannot move while sitting", "вы не можете двигаться пока сидите",
                      "вы не можете двигаться, пока сидите"}:
        return ChatMessage("sitting_block", text, at)
    if normalized.startswith("you use "):
        return ChatMessage("skill_used", text, at)
    if normalized in {"over-hit", "over hit", "overhit"}:
        return ChatMessage("overhit", text, at)
    restored = re.fullmatch(r"(\d+) hp has been restored", normalized)
    if restored:
        return ChatMessage("hp_restored", text, at, int(restored[1]))
    experience = re.fullmatch(r"you have acquired (\d+) xp and (\d+) sp", normalized)
    if experience:
        return ChatMessage("experience", text, at, int(experience[1]), int(experience[2]))
    if normalized.startswith("use soulshot:"):
        return ChatMessage("soulshot", text, at)
    return ChatMessage("unknown", text, at)


class ChatTracker:
    def __init__(self) -> None:
        self.previous: list[str] | None = None
        self.last_at: float | None = None

    def update(self, lines: list[str], at: float) -> list[ChatMessage]:
        original = lines[-40:]
        lines = [" ".join(line.casefold().split()).rstrip(".! ") for line in original]
        previous = self.previous
        self.previous = lines
        stale = self.last_at is None or not 0 <= at - self.last_at <= 3
        self.last_at = at
        # Первый кадр, пропуск OCR и полная смена истории дают только новую базу.
        if stale or not previous or not lines:
            return []
        overlap = next((size for size in range(min(len(previous), len(lines)), 0, -1)
                        if previous[-size:] == lines[:size]), 0)
        if not overlap:
            return []
        return [parse_message(line, at) for line in original[overlap:]]


class SystemChatOCR:
    def __init__(self, profile: BarProfile, numbers: NumbersReader) -> None:
        self.profile = profile
        self.numbers = numbers
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.chat_tracker = ChatTracker()
        self.chat_events: deque[ChatMessage] = deque(maxlen=32)
        self.chat_generation = 0

    def reset_chat(self) -> None:
        with self.lock:
            self.chat_generation += 1
            self.chat_tracker = ChatTracker()
            self.chat_events.clear()

    def chat_ready(self) -> bool:
        if not SYSTEM_CHAT_ENABLED:
            return False
        with self.lock:
            return self.chat_tracker.last_at is not None and 0 <= time.monotonic() - self.chat_tracker.last_at <= 3

    def take_chat_events(self) -> list[ChatMessage]:
        with self.lock:
            events = list(self.chat_events) if SYSTEM_CHAT_ENABLED else []
            self.chat_events.clear()
            return events

    def read_chat(self, image: np.ndarray, at: float) -> None:
        if not SYSTEM_CHAT_ENABLED:
            self.reset_chat()
            publish_chat_preview(at, [], "Распознавание системного чата временно отключено")
            return
        if not self.profile.system_chat_enabled or self.profile.system_chat is None:
            publish_chat_preview(at, [], "OCR выключен" if not self.profile.system_chat_enabled
                                 else "Область системного чата не размечена")
            return
        with self.lock:
            generation = self.chat_generation
        try:
            crop = self.profile.crop(image, self.profile.system_chat)
            rows = sorted(self.numbers.detect(crop), key=lambda row: (min(p[1] for p in row[0]),
                                                                    min(p[0] for p in row[0])))
            # Сомнительная строка не должна превратить старую ошибку в новое событие.
            grouped: list[tuple[float, float, list[tuple[float, str]]]] = []
            for box, text, _ in rows:
                top, bottom = min(p[1] for p in box), max(p[1] for p in box)
                center, height = (top + bottom) / 2, max(1, bottom - top)
                part = (min(p[0] for p in box), text)
                if grouped and abs(grouped[-1][0] - center) <= min(grouped[-1][1], height) / 2:
                    grouped[-1][2].append(part)
                else:
                    grouped.append((center, height, [part]))
            lines = [" ".join(text for _, text in sorted(parts)) for _, _, parts in grouped][-40:]
            uncertain = any(score < .9 for _, _, score in rows)
            publish_chat_preview(at, lines, "Низкая уверенность OCR — без автоматической реакции"
                                 if uncertain else "OCR: распознано" if lines else "OCR: текст не найден")
            if uncertain:
                self.reset_chat()
                return
            with self.lock:
                if generation != self.chat_generation or self.stop_event.is_set():
                    return
                if not 0 <= time.monotonic() - at <= 3:
                    self.chat_tracker = ChatTracker()
                    return
                events = self.chat_tracker.update(lines, at)
                self.chat_events.extend(events)
            for event in events:
                chat_logger.info(event.text, extra={"chat_kind": event.kind, "chat_amount": event.amount,
                                                   "chat_sp": event.sp, "chat_at": event.at})
        except Exception:
            self.reset_chat()
            publish_chat_preview(at, [], "Ошибка OCR системного чата; подробности в журнале приложения")
            logger.warning("OCR системного чата временно недоступен", exc_info=True)

    def close(self) -> None:
        self.stop_event.set()
        self.reset_chat()
