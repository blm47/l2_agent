"""
Изолированный запрос foreground: зависшая очередь игры не блокирует контроллер.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import logging
from pathlib import Path
import subprocess
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

    from hid_pilot.desktop import Target

logger = logging.getLogger("hid_pilot.activation")


def request_activation(source: Target, target: Target, stopped: Event,
                       hard_stop_ready: Callable[[], bool], timeout: float,
                       strategy: str = "foreground") -> None:
    """
    Ждёт helper с ограниченным бюджетом; при STOP дочерний процесс завершается.
    """
    if strategy not in {"foreground", "task_switch", "attached_focus"}:
        raise ValueError("Неизвестная стратегия активации окна")
    payload = json.dumps({"source": source.model_dump(), "target": target.model_dump(), "strategy": strategy})
    entry = ([sys.executable, "--activate-window"] if getattr(sys, "frozen", False)
             else [sys.executable, str(Path(__file__).resolve()), "--activate-window"])
    process = subprocess.Popen(
        [*entry, payload], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    deadline = time.monotonic() + timeout
    output_consumed = False
    try:
        while process.poll() is None:
            if stopped.is_set() or not hard_stop_ready():
                raise RuntimeError("Переключение окна отменено")
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Windows не завершила активацию окна «{target.title}» за {timeout:g} с")
            stopped.wait(.01)
        if stopped.is_set() or not hard_stop_ready():
            raise RuntimeError("Переключение окна отменено")
        try:
            output, _ = process.communicate(timeout=.1)
            output_consumed = True
        except subprocess.TimeoutExpired as exc:
            if process.returncode != 0:
                raise RuntimeError("Helper активации завершился с ошибкой; чтение ответа превысило timeout") from exc
            # Успешный exit code допускается и без stdout; контроллер отдельно проверит foreground.
            logger.warning("Helper активации завершён успешно, но ответ задержан; проверяем фокус окна")
            output = b""
        try:
            # У windowed EXE stdout может отсутствовать. Код 0 helper возвращает
            # только после проверки HWND/PID/геометрии и фактического foreground.
            result = json.loads(output) if output else {"focused": process.returncode == 0, "method": "native"}
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError("Helper активации не вернул корректный результат") from exc
        if process.returncode != 0 or not result.get("focused"):
            raise RuntimeError(f"Не удалось активировать «{target.title}»: {result.get('error', 'отказ Windows')}")
        logger.info("Активация через helper: %s [PID %s], метод %s",
                    target.title, target.pid, result.get("method"))
    finally:
        if process.poll() is None:
            process.kill()
        if not output_consumed:
            try:
                process.communicate(timeout=.5)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                # Ошибка очистки pipe не должна перекрывать исходную причину отказа или STOP.
                logger.warning("Не удалось дочитать pipe завершённого helper активации", exc_info=True)


def _activate_native(payload: dict) -> dict:
    strategy = payload.get("strategy", "foreground")
    if strategy not in {"foreground", "task_switch", "attached_focus"}:
        raise ValueError("Неизвестная стратегия активации окна")
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "GetForegroundWindow": ([], wintypes.HWND),
        "IsWindow": ([wintypes.HWND], wintypes.BOOL),
        "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
        "IsIconic": ([wintypes.HWND], wintypes.BOOL),
        "GetWindowThreadProcessId": ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        "GetClientRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        "ClientToScreen": ([wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        "SetForegroundWindow": ([wintypes.HWND], wintypes.BOOL),
        "BringWindowToTop": ([wintypes.HWND], wintypes.BOOL),
        "SetActiveWindow": ([wintypes.HWND], wintypes.HWND),
        "SetFocus": ([wintypes.HWND], wintypes.HWND),
        "AttachThreadInput": ([wintypes.DWORD, wintypes.DWORD, wintypes.BOOL], wintypes.BOOL),
        "PeekMessageW": ([ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                          wintypes.UINT, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
        "SetProcessDpiAwarenessContext": ([ctypes.c_void_p], wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(user, name)
        function.argtypes, function.restype = arguments, result
    kernel.GetCurrentThreadId.argtypes = []
    kernel.GetCurrentThreadId.restype = wintypes.DWORD
    user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    source, target = payload["source"], payload["target"]

    def validate(window: dict) -> int:
        hwnd = window["hwnd"]
        if not user.IsWindow(hwnd) or not user.IsWindowVisible(hwnd) or user.IsIconic(hwnd):
            raise RuntimeError("Окно закрыто, скрыто или свёрнуто")
        pid = wintypes.DWORD()
        thread = user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not thread or pid.value != window["pid"]:
            raise RuntimeError("Окно сменило процесс")
        rect, origin = wintypes.RECT(), wintypes.POINT()
        if not user.GetClientRect(hwnd, ctypes.byref(rect)) or not user.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise RuntimeError("Не удалось проверить геометрию окна")
        current = [origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom]
        if current != window["rect"]:
            raise RuntimeError("Геометрия окна изменилась")
        return thread

    target_thread = validate(target)
    if user.GetForegroundWindow() == target["hwnd"]:
        return {"focused": True, "method": "already_active"}
    source_thread = validate(source)
    if user.GetForegroundWindow() != source["hwnd"]:
        raise RuntimeError("Пользователь сменил активное окно; активация отменена")
    method = "direct"
    if strategy == "task_switch":
        # Запасной системный переключатель выбирает конкретный HWND,
        # не отправляя клавиатурное сочетание в произвольное приложение.
        switch = user.SwitchToThisWindow
        switch.argtypes = [wintypes.HWND, wintypes.BOOL]
        switch.restype = None
        switch(target["hwnd"], True)
        method = "task_switch"
    elif strategy == "attached_focus" or (
        not user.SetForegroundWindow(target["hwnd"]) and user.GetForegroundWindow() != target["hwnd"]
    ):
        # Присоединение очередей может зависнуть внутри Win32. Только helper
        # выполняет его: контроллер завершит весь процесс по timeout или STOP.
        validate(target)
        if user.GetForegroundWindow() != source["hwnd"]:
            raise RuntimeError("Активное окно изменилось перед повтором")
        message = wintypes.MSG()
        user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        current_thread = kernel.GetCurrentThreadId()
        attached = []
        try:
            # После сброса связываем также очередь назначения: одного повторного
            # SetForegroundWindow после переподключения COM недостаточно.
            threads = (source_thread, target_thread) if strategy == "attached_focus" else (source_thread,)
            for thread in dict.fromkeys(threads):
                if thread == current_thread:
                    continue
                if not user.AttachThreadInput(current_thread, thread, True):
                    raise RuntimeError(f"AttachThreadInput отклонён, код {ctypes.get_last_error()}")
                attached.append(thread)
            validate(target)
            if user.GetForegroundWindow() != source["hwnd"]:
                raise RuntimeError("Активное окно изменилось при присоединении очереди")
            user.SetForegroundWindow(target["hwnd"])
            method = "attached_queue"
            if strategy == "attached_focus":
                for operation in (user.BringWindowToTop, user.SetActiveWindow, user.SetFocus):
                    validate(target)
                    if user.GetForegroundWindow() not in (source["hwnd"], target["hwnd"]):
                        raise RuntimeError("Пользователь сменил активное окно при восстановлении фокуса")
                    operation(target["hwnd"])
                method = "attached_focus"
        finally:
            for thread in reversed(attached):
                user.AttachThreadInput(current_thread, thread, False)
    deadline = time.monotonic() + .4
    while user.GetForegroundWindow() != target["hwnd"] and time.monotonic() < deadline:
        time.sleep(.01)
    validate(target)
    focused = user.GetForegroundWindow() == target["hwnd"]
    result = {"focused": focused, "method": method}
    if not focused:
        foreground = user.GetForegroundWindow()
        foreground_pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(foreground, ctypes.byref(foreground_pid))
        result["error"] = (
            f"Windows не подтвердила foreground (метод {method}; "
            f"активно HWND {foreground}, PID {foreground_pid.value}; "
            f"ожидалось HWND {target['hwnd']}, PID {target['pid']})"
        )
    return result


def helper_main(arguments: list[str]) -> int:
    try:
        if len(arguments) != 2 or arguments[0] != "--activate-window":
            raise ValueError("Некорректный запуск helper активации")
        result = _activate_native(json.loads(arguments[1]))
    except Exception as exc:
        result = {"focused": False, "error": str(exc)}
    # stdout используется как ограниченный IPC-ответ, а не как журнал приложения.
    if sys.stdout is not None:
        sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True).encode("ascii"))
        sys.stdout.buffer.flush()
    return 0 if result.get("focused") else 1


if __name__ == "__main__":
    raise SystemExit(helper_main(sys.argv[1:]))
