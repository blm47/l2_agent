import ctypes
import logging
import threading
from collections.abc import Callable
from ctypes import wintypes

logger = logging.getLogger("l2_agent.hard_stop")


class HardStopHotkey:
    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._stop = threading.Event()
        self._registered = threading.Event()
        self._ready = threading.Event()
        self.error = ""
        self._thread = threading.Thread(target=self._run, name="HardStop", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2)

    @property
    def available(self) -> bool:
        return self._registered.is_set() and self._thread.is_alive()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.PeekMessageW.restype = wintypes.BOOL
        registered = False
        try:
            # F10 без модификаторов; повторное срабатывание при удержании отключено.
            if not user32.RegisterHotKey(None, 1, 0x4000, 0x79):
                raise ctypes.WinError(ctypes.get_last_error())
            registered = True
            self._registered.set()
            self._ready.set()
            logger.info("Глобальный hard-stop зарегистрирован: F10")
            message = wintypes.MSG()
            while not self._stop.is_set():
                while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    if message.message == 0x0312 and message.wParam == 1:
                        logger.warning("Hard-stop: нажата F10")
                        self._callback()
                self._stop.wait(0.01)
        except Exception as exc:
            self.error = str(exc)
            logger.exception("Hard-stop недоступен")
        finally:
            self._registered.clear()
            self._ready.set()
            if registered:
                user32.UnregisterHotKey(None, 1)
            if not self._stop.is_set():
                self._callback()
