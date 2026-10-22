"""
Последний результат OCR чата для GUI без файлового polling и очереди кадров.
"""

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class ChatPreview:
    at: float
    lines: tuple[str, ...]
    status: str


_lock = threading.Lock()
_latest: ChatPreview | None = None


def publish_chat_preview(at: float, lines: list[str], status: str) -> None:
    global _latest
    with _lock:
        _latest = ChatPreview(at, tuple(lines[-40:]), status)


def latest_chat_preview() -> ChatPreview | None:
    with _lock:
        return _latest
