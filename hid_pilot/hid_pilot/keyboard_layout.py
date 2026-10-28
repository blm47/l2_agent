"""
Проверка английской раскладки игрового окна без клавиатурных сочетаний.
"""

import logging
import time
from collections.abc import Callable
from threading import Event

import win32api
import win32gui
import win32process

from hid_pilot.desktop import Target, snapshot

logger = logging.getLogger("hid_pilot.keyboard_layout")


def ensure_english_layout(target: Target, stopped: Event,
                          verify: Callable[[], None], timeout: float = .75) -> None:
    """
    Запрашивает ENG у конкретного окна и ждёт подтверждения его потока.
    """
    def checked_thread() -> int:
        verify()
        current = snapshot(target.hwnd)
        if (current.pid, current.rect) != (target.pid, target.rect):
            raise RuntimeError("Окно изменилось при проверке раскладки")
        thread, pid = win32process.GetWindowThreadProcessId(target.hwnd)
        if not thread or pid != target.pid:
            raise RuntimeError("Окно сменило процесс при проверке раскладки")
        return thread

    thread = checked_thread()
    # PRIMARYLANGID: подходит английский любой установленной локали.
    if win32api.GetKeyboardLayout(thread) & 0x3FF == 0x09:
        logger.info("Раскладка «%s»: ENG уже активна", target.title)
        return
    english = next((layout for layout in win32api.GetKeyboardLayoutList()
                    if layout & 0x3FF == 0x09), None)
    if english is None:
        raise RuntimeError("Английская раскладка не установлена в Windows; добавьте ENG перед стартом фарма")
    checked_thread()
    # PostMessage не ждёт зависшую очередь игры и не генерирует нажатий.
    win32gui.PostMessage(target.hwnd, 0x0050, 0, english)
    deadline = time.monotonic() + timeout
    while True:
        if checked_thread() != thread:
            raise RuntimeError("Поток окна изменился при переключении раскладки")
        if win32api.GetKeyboardLayout(thread) & 0x3FF == 0x09:
            logger.info("Раскладка «%s»: переключена на ENG", target.title)
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Окно «{target.title}» не подтвердило ENG; старт фарма отменён")
        stopped.wait(.01)
