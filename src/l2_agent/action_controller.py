import ctypes
import logging
import threading
import time
from collections.abc import Callable
from ctypes import wintypes

import win32api
import win32gui

from l2_agent.action_validator import ALLOWED_KEYS, MOUSE_BUTTONS, ActionRequest, ActionValidator
from l2_agent.geometry import ClientRect, NormalizedPoint
from l2_agent.windows import ParsecWindowManager, WindowSnapshot

logger = logging.getLogger("l2_agent.action_controller")

# Физические позиции клавиш US QWERTY, как в рабочем примере для Parsec.
# Все разрешённые здесь клавиши имеют обычный scan code без префикса E0.
KEY_SCAN_CODES = {
    "W": 0x11,
    "A": 0x1E,
    "S": 0x1F,
    "D": 0x20,
    "Q": 0x10,
    "E": 0x12,
    "R": 0x13,
    "I": 0x17,
    "M": 0x32,
    **{key: index + 0x02 for index, key in enumerate("1234567890")},
    **{f"F{index}": 0x3A + index for index in range(1, 9)},
    "SPACE": 0x39,
    "TAB": 0x0F,
    "ESC": 0x01,
}


class KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("vk", wintypes.WORD),
        ("scan", wintypes.WORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra", ctypes.c_size_t),
    ]


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("data", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra", ctypes.c_size_t),
    ]


class InputUnion(ctypes.Union):
    _fields_ = [("keyboard", KeyboardInput), ("mouse", MouseInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("value", InputUnion)]


class InputEnvironment:
    def __init__(self) -> None:
        self.manager = ParsecWindowManager()

    def snapshot(self, target: tuple[int, int]) -> WindowSnapshot:
        return self.manager.snapshot(*target)

    def desktop(self) -> ClientRect:
        x, y, width, height = (win32api.GetSystemMetrics(index) for index in (76, 77, 78, 79))
        return ClientRect(left=x, top=y, right=x + width, bottom=y + height)

    def pressed(self, vk: int) -> bool:
        return bool(win32api.GetAsyncKeyState(vk) & 0x8000)

    def modifiers_pressed(self) -> bool:
        return any(self.pressed(vk) for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))

    def cursor(self) -> tuple[int, int]:
        return win32api.GetCursorPos()

    def owns_point(self, hwnd: int, point: tuple[int, int]) -> bool:
        hit = win32gui.WindowFromPoint(point)
        return bool(hit and win32gui.GetAncestor(hit, 2) == hwnd)


class ActionController:
    def __init__(self, environment: InputEnvironment | None = None) -> None:
        self.environment = environment or InputEnvironment()
        self.validator = ActionValidator()
        self.hard_stop_ready: Callable[[], bool] = lambda: False
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._cancel.set()
        self._shutdown = threading.Event()
        self._target: tuple[int, int] | None = None
        self._armed = False
        self._reason = "STOP"
        self._keys: dict[str, tuple[float, int]] = {}
        self._buttons: dict[str, tuple[float, int]] = {}
        self._lease = 0
        self._epoch = 0
        self._last_action = float("-inf")
        self._retry_release_at = 0.0
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
        self._user32.SendInput.restype = wintypes.UINT
        self._watchdog = threading.Thread(target=self._watch, name="InputWatchdog", daemon=True)
        self._watchdog.start()

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    @property
    def state(self) -> str:
        with self._lock:
            return self._reason

    @property
    def armed(self) -> bool:
        with self._lock:
            return self._armed and not self._cancel.is_set()

    def arm(self, target: tuple[int, int]) -> None:
        with self._lock:
            if self._shutdown.is_set() or not self.hard_stop_ready():
                raise ValueError("Hard-stop не готов или контроллер закрыт")
            if self._keys or self._buttons:
                raise ValueError("Не все клавиши или кнопки освобождены")
            snapshot = self.environment.snapshot(target)
            if snapshot.minimized or snapshot.rect is None:
                raise ValueError("Окно Parsec недоступно")
            self._target = target
            self._epoch += 1
            self._cancel.clear()
            self._armed = True
            self._reason = "READY — только ручные тесты M0"
            logger.info("Ручной ввод разрешён для Parsec HWND=%s", target[0])

    def stop(self, reason: str = "STOP") -> None:
        self._cancel.set()
        with self._lock:
            self._epoch += 1
            self._armed = False
            self._reason = reason
            self.release_all()
            logger.info("Ввод остановлен: %s", reason)

    def _send(self, event: Input) -> None:
        if self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
            raise OSError(f"SendInput не отправил событие; Win32 error={ctypes.get_last_error()}")

    def _key_event(self, key: str, up: bool) -> Input:
        return Input(
            type=1,
            value=InputUnion(
                keyboard=KeyboardInput(
                    vk=0,
                    scan=KEY_SCAN_CODES[key],
                    flags=0x0008 | (0x0002 if up else 0),
                )
            ),
        )

    def _mouse_event(self, flags: int, x: int = 0, y: int = 0) -> Input:
        return Input(type=0, value=InputUnion(mouse=MouseInput(dx=x, dy=y, flags=flags)))

    def _validate(self) -> ClientRect:
        if self._target is None:
            raise ValueError("Окно Parsec не выбрано")
        return self.validator.validate(
            self.environment.snapshot(self._target),
            armed=self.armed,
            hard_stop_ready=self.hard_stop_ready(),
            modifiers_pressed=self.environment.modifiers_pressed(),
            desktop=self.environment.desktop(),
        )

    def execute(self, request: ActionRequest) -> int:
        with self._lock:
            try:
                # Повторная проверка защищает от model_construct/model_copy без валидации.
                request = ActionRequest.model_validate(request.model_dump())
                rect = self._validate()
                if self._keys or self._buttons:
                    raise ValueError("В M0 допускается только одно удержание одновременно")
                now = time.monotonic()
                if now - self._last_action < 0.05:
                    raise ValueError("Превышен лимит 20 действий в секунду")
                self._lease += 1
                lease = self._lease
                if request.kind == "key_down":
                    assert request.key is not None
                    if self.environment.pressed(ALLOWED_KEYS[request.key]):
                        raise ValueError("Клавиша уже удерживается пользователем")
                    event = self._key_event(request.key, False)
                    self._keys[request.key] = (now + request.duration_ms / 1000, lease)
                else:
                    if request.kind == "move":
                        assert request.point is not None
                        point = rect.to_screen(request.point)
                    else:
                        point = self.environment.cursor()
                    assert self._target is not None
                    if not rect.contains(*point) or not self.environment.owns_point(
                        self._target[0], point
                    ):
                        raise ValueError("Точка находится вне Parsec или перекрыта другим окном")
                    if request.kind == "move":
                        desktop = self.environment.desktop()
                        # Центр пикселя в абсолютной сетке виртуального рабочего стола.
                        x = int((point[0] - desktop.left + 0.5) * 65536 / desktop.width)
                        y = int((point[1] - desktop.top + 0.5) * 65536 / desktop.height)
                        event = self._mouse_event(0x0001 | 0x8000 | 0x4000, x, y)
                    else:
                        assert request.button is not None
                        down, _up, vk = MOUSE_BUTTONS[request.button]
                        if self.environment.pressed(vk):
                            raise ValueError("Кнопка мыши уже удерживается пользователем")
                        event = self._mouse_event(down)
                        self._buttons[request.button] = (now + request.duration_ms / 1000, lease)
                if self._cancel.is_set():
                    raise ValueError("Получен STOP")
                if self._validate() != rect:
                    raise ValueError("Геометрия Parsec изменилась перед отправкой ввода")
                if request.kind != "key_down":
                    assert self._target is not None
                    if request.kind == "mouse_down" and self.environment.cursor() != point:
                        raise ValueError("Курсор переместился перед нажатием")
                    if not self.environment.owns_point(self._target[0], point):
                        raise ValueError("Точка Parsec перекрыта перед отправкой ввода")
                self._send(event)
                self._last_action = now
                return lease
            except Exception:
                self.stop("ERROR — ввод заблокирован")
                logger.exception("Действие отклонено или не выполнено")
                raise

    def key_down(self, key: str, duration_ms: int = 100) -> int:
        try:
            request = ActionRequest(kind="key_down", key=key, duration_ms=duration_ms)
        except ValueError:
            self.stop("ERROR — неверные параметры клавиши")
            raise
        return self.execute(request)

    def mouse_button_down(self, button: str, duration_ms: int = 100) -> int:
        try:
            request = ActionRequest(kind="mouse_down", button=button, duration_ms=duration_ms)
        except ValueError:
            self.stop("ERROR — неверные параметры кнопки мыши")
            raise
        return self.execute(request)

    def key_press(self, key: str, duration_ms: int = 100) -> None:
        lease = self.key_down(key, duration_ms)
        self._cancel.wait(duration_ms / 1000)
        with self._lock:
            if self._keys.get(key, (0, -1))[1] == lease:
                self.key_up(key)

    def click(self, button: str = "left", duration_ms: int = 100) -> None:
        lease = self.mouse_button_down(button, duration_ms)
        self._cancel.wait(duration_ms / 1000)
        with self._lock:
            if self._buttons.get(button, (0, -1))[1] == lease:
                self.mouse_button_up(button)

    def mouse_move_absolute_client(self, x_norm: float, y_norm: float) -> None:
        try:
            request = ActionRequest(kind="move", point=NormalizedPoint(x=x_norm, y=y_norm))
        except ValueError:
            self.stop("ERROR — неверные координаты")
            raise
        self.execute(request)

    def mouse_move_relative(self, dx: int, dy: int) -> None:
        with self._lock:
            try:
                if type(dx) is not int or type(dy) is not int:
                    raise ValueError("Смещение мыши должно быть целым числом")
                rect = self._validate()
                x, y = self.environment.cursor()
                x, y = x + dx, y + dy
                if not rect.contains(x, y):
                    raise ValueError("Перемещение выходит за пределы Parsec")
                self.mouse_move_absolute_client(
                    (x - rect.left) / max(1, rect.width - 1),
                    (y - rect.top) / max(1, rect.height - 1),
                )
            except Exception:
                self.stop("ERROR — перемещение мыши отклонено")
                raise

    def key_up(self, key: str) -> None:
        with self._lock:
            if key in self._keys:
                try:
                    self._send(self._key_event(key, True))
                except Exception:
                    self._armed = False
                    self._cancel.set()
                    self._reason = "ERROR — клавиша не освобождена"
                    raise
                del self._keys[key]

    def mouse_button_up(self, button: str) -> None:
        with self._lock:
            if button in self._buttons:
                try:
                    self._send(self._mouse_event(MOUSE_BUTTONS[button][1]))
                except Exception:
                    self._armed = False
                    self._cancel.set()
                    self._reason = "ERROR — кнопка мыши не освобождена"
                    raise
                del self._buttons[button]

    def release_all_keys(self) -> None:
        with self._lock:
            for key in list(self._keys):
                try:
                    self.key_up(key)
                except Exception:
                    self._armed = False
                    self._cancel.set()
                    self._reason = "ERROR — не удалось освободить клавишу"
                    self._retry_release_at = time.monotonic() + 0.25
                    logger.exception("Не удалось освободить %s; watchdog повторит попытку", key)

    def release_all_mouse_buttons(self) -> None:
        with self._lock:
            for button in list(self._buttons):
                try:
                    self.mouse_button_up(button)
                except Exception:
                    self._armed = False
                    self._cancel.set()
                    self._reason = "ERROR — не удалось освободить кнопку мыши"
                    self._retry_release_at = time.monotonic() + 0.25
                    logger.exception("Не удалось освободить %s; watchdog повторит попытку", button)

    def release_all(self) -> None:
        self.release_all_keys()
        self.release_all_mouse_buttons()

    def _watch(self) -> None:
        while not self._shutdown.wait(0.01):
            with self._lock:
                try:
                    if self._armed and not self.hard_stop_ready():
                        self.stop("EMERGENCY — hard-stop недоступен")
                    if not self._keys and not self._buttons:
                        continue
                    if not self.armed:
                        if time.monotonic() >= self._retry_release_at:
                            self.release_all()
                        continue
                    rect = self._validate()
                    if self._buttons:
                        point = self.environment.cursor()
                        assert self._target is not None
                        if not rect.contains(*point) or not self.environment.owns_point(
                            self._target[0], point
                        ):
                            raise ValueError("Курсор покинул Parsec во время удержания")
                    now = time.monotonic()
                    for key, (deadline, _lease) in list(self._keys.items()):
                        if now >= deadline:
                            self.key_up(key)
                    for button, (deadline, _lease) in list(self._buttons.items()):
                        if now >= deadline:
                            self.mouse_button_up(button)
                except Exception:
                    logger.exception("Watchdog остановил ввод")
                    self.stop("EMERGENCY — проверка ввода не пройдена")

    def close(self) -> None:
        self.stop("STOP — закрытие приложения")
        self._shutdown.set()
        self._watchdog.join(timeout=2)
        self.release_all()
