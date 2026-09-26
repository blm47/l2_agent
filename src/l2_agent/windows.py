import ctypes
import logging
from ctypes import wintypes
from pathlib import PureWindowsPath

import pywintypes
import win32api
import win32gui
import win32process
from pydantic import BaseModel, ConfigDict

from l2_agent.geometry import ClientRect

logger = logging.getLogger("l2_agent.windows")

def process_image_name(handle: int) -> str:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel32.QueryFullProcessImageNameW
    query.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    query.restype = wintypes.BOOL
    buffer = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(buffer))
    if not query(handle, 0, buffer, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.value


class WindowSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    hwnd: int
    pid: int
    title: str
    executable_name: str = ""
    minimized: bool
    focused: bool
    rect: ClientRect | None


class GameWindowManager:
    def _executable_name(self, pid: int) -> str:
        handle = win32api.OpenProcess(0x1000, False, pid)
        try:
            executable = process_image_name(int(handle))
            return PureWindowsPath(executable).name.lower()
        finally:
            handle.Close()

    def snapshot(self, hwnd: int, expected_pid: int | None = None) -> WindowSnapshot:
        if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
            raise ValueError("Окно закрыто или скрыто")
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if expected_pid is not None and pid != expected_pid:
            raise ValueError("Окно больше не принадлежит выбранному процессу")
        try:
            executable_name = self._executable_name(pid)
        except (pywintypes.error, OSError):
            # Имя процесса — подпись в списке, его недоступность не скрывает окно.
            executable_name = ""
        minimized = bool(win32gui.IsIconic(hwnd))
        rect = None
        if not minimized:
            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            if right > left and bottom > top:
                x1, y1 = win32gui.ClientToScreen(hwnd, (left, top))
                x2, y2 = win32gui.ClientToScreen(hwnd, (right, bottom))
                rect = ClientRect(left=x1, top=y1, right=x2, bottom=y2)
        return WindowSnapshot(
            hwnd=hwnd,
            pid=pid,
            title=win32gui.GetWindowText(hwnd),
            executable_name=executable_name,
            minimized=minimized,
            focused=win32gui.GetForegroundWindow() == hwnd,
            rect=rect,
        )

    def discover(self) -> list[WindowSnapshot]:
        windows: list[WindowSnapshot] = []

        def collect(hwnd: int, _: object) -> bool:
            if win32gui.IsWindowVisible(hwnd):
                try:
                    windows.append(self.snapshot(hwnd))
                except (pywintypes.error, OSError, ValueError):
                    # Недоступные и закрывшиеся во время обхода окна пропускаются.
                    pass
            return True

        win32gui.EnumWindows(collect, None)
        logger.info("Найдено видимых окон: %s", len(windows))
        return windows
