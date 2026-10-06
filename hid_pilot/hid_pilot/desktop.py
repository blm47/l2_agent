import logging

import cv2
import numpy as np
import pywintypes
import win32api
import win32gui
import win32process
import win32ui
from pydantic import BaseModel, ConfigDict


class FocusLost(RuntimeError):
    pass


class Target(BaseModel):
    model_config = ConfigDict(frozen=True)
    hwnd: int
    pid: int
    title: str
    rect: tuple[int, int, int, int]


def activate(target: Target) -> Target:
    current = snapshot(target.hwnd)
    if current.pid != target.pid:
        raise RuntimeError("Выбранное окно сменило процесс")
    # Запрос от START или настройки областей; watchdog фокус не перехватывает.
    win32gui.SetForegroundWindow(target.hwnd)
    # Windows может завершить переключение позже. Capture и ввод проверяют
    # фокус после предусмотренной вызывающим кодом задержки.
    return current


def snapshot(hwnd: int) -> Target:
    if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
        raise ValueError("Окно недоступно")
    if win32gui.IsIconic(hwnd):
        raise ValueError("Окно свёрнуто")
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    _, _, width, height = win32gui.GetClientRect(hwnd)
    if width < 2 or height < 2:
        raise ValueError("Клиентская область пуста")
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    return Target(hwnd=hwnd, pid=pid, title=win32gui.GetWindowText(hwnd),
                  rect=(left, top, left + width, top + height))


def windows() -> list[Target]:
    result = []

    def collect(hwnd: int, _: object) -> bool:
        try:
            item = snapshot(hwnd)
            if item.title:
                result.append(item)
        except (pywintypes.error, OSError, ValueError):
            # Закрывающиеся и недоступные окна пропускаются при перечислении.
            logging.getLogger("hid_pilot.desktop").debug("Окно недоступно", exc_info=True)
        return True

    win32gui.EnumWindows(collect, None)
    return result


class Desktop:
    def __init__(self, target: Target) -> None:
        self.target = target

    def check(self) -> None:
        if not win32gui.IsWindow(self.target.hwnd):
            raise RuntimeError("Выбранное окно закрыто")
        if win32process.GetWindowThreadProcessId(self.target.hwnd)[1] != self.target.pid:
            raise RuntimeError("Выбранное окно сменило процесс")
        if win32gui.IsIconic(self.target.hwnd):
            raise FocusLost("Окно свёрнуто; автопауза")
        current = snapshot(self.target.hwnd)
        if current.pid != self.target.pid or current.rect != self.target.rect:
            raise RuntimeError("Окно или его геометрия изменились")
        if win32gui.GetForegroundWindow() != self.target.hwnd:
            foreground = win32gui.GetWindowText(win32gui.GetForegroundWindow())
            raise FocusLost(
                f"Ожидалось окно «{self.target.title}», активно «{foreground}». Автопауза"
            )

    def cursor(self) -> tuple[int, int]:
        return win32api.GetCursorPos()

    def point(self, x: float, y: float) -> tuple[int, int]:
        if not 0 <= x <= 1 or not 0 <= y <= 1:
            raise ValueError("Точка за пределами окна")
        left, top, right, bottom = self.target.rect
        return left + round(x * (right - left - 1)), top + round(y * (bottom - top - 1))

    def check_point(self, point: tuple[int, int]) -> None:
        left, top, right, bottom = self.target.rect
        x, y = point
        if not left <= x < right or not top <= y < bottom:
            raise RuntimeError("Курсор вне клиентской области")
        child = win32gui.WindowFromPoint(point)
        if child != self.target.hwnd and not win32gui.IsChild(self.target.hwnd, child):
            raise RuntimeError("Точку перекрывает другое окно")

    def capture(self) -> np.ndarray:
        self.check()
        left, top, right, bottom = self.target.rect
        width, height = right - left, bottom - top
        if width * height > 16_000_000:
            raise ValueError("Клиентская область слишком большая для capture")
        handle = win32gui.GetDC(0)
        source = destination = bitmap = None
        try:
            source = win32ui.CreateDCFromHandle(handle)
            destination = source.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(source, width, height)
            destination.SelectObject(bitmap)
            destination.BitBlt((0, 0), (width, height), source, (left, top), 0x00CC0020)
            pixels = np.frombuffer(bitmap.GetBitmapBits(True), dtype=np.uint8)
            image = pixels.reshape(height, width, 4)[:, :, :3].copy()
        finally:
            if destination is not None:
                destination.DeleteDC()
            if source is not None:
                source.DeleteDC()
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetHandle())
            win32gui.ReleaseDC(0, handle)
        self.check()
        return image


def match_image(image: np.ndarray, template: np.ndarray, threshold: float) -> tuple[float, float] | None:
    height, width = template.shape[:2]
    if height > image.shape[0] or width > image.shape[1]:
        return None
    # SQDIFF работает и для одноцветного шаблона, в отличие от CCOEFF.
    scores = cv2.matchTemplate(image, template, cv2.TM_SQDIFF_NORMED)
    minimum, _, location, _ = cv2.minMaxLoc(scores)
    if not np.isfinite(minimum) or 1 - minimum < threshold:
        return None
    return ((location[0] + (width - 1) / 2) / (image.shape[1] - 1),
            (location[1] + (height - 1) / 2) / (image.shape[0] - 1))
