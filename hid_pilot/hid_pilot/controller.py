import logging
import random
import threading
import time
import win32api
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import numpy as np

from hid_pilot.desktop import ActivationDenied, Desktop, FocusLost, Target, activate, snapshot
from hid_pilot.mouse_path import mouse_path
from hid_pilot.script import KEYS
from hid_pilot.transport import PicoTransport
from hid_pilot.window_activation import request_activation
from hid_pilot.keyboard_layout import ensure_english_layout

logger = logging.getLogger("hid_pilot.controller")


class Stopped(Exception):
    pass


class ActionController:
    """
    Единственный путь к HID; watchdog проверяет окно даже во время CV.
    """

    def __init__(self, desktop: Desktop, transport: PicoTransport,
                 hard_stop_ready: Callable[[], bool], notify: Callable[[str], None] | None = None) -> None:
        self.desktop = desktop
        self.transport = transport
        self.hard_stop_ready = hard_stop_ready
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.paused = threading.Event()
        self.manual_paused = threading.Event()
        self.pause_generation = 0
        self.pause_started: float | None = None
        self.paused_seconds = 0.
        self.notify = notify or (lambda _: None)
        self.monitor: threading.Thread | None = None
        self.error = ""
        self.humanize = False
        self.debug_capture = False
        self.activation_strategies: dict[tuple[int, int, int, int], str] = {}

    def check(self) -> None:
        if self.stopped.is_set():
            raise Stopped(self.error or "Сценарий остановлен")
        if not self.hard_stop_ready():
            raise RuntimeError("Hard-stop недоступен")
        self.desktop.check()

    def start(self) -> None:
        with self.lock:
            self.sync_pause()
            if not self.paused.is_set():
                self.transport.arm()
        self.monitor = threading.Thread(target=self._watch, daemon=True)
        self.monitor.start()

    def prepare_keyboard(self, targets: tuple[Target, ...]) -> None:
        """
        Проверяет раскладку всех игровых окон до первого ARM.
        """
        def verify() -> None:
            self.check()
            if self.manual_paused.is_set():
                raise Stopped("Подготовка раскладки отменена ручной паузой")

        with self.lock:
            try:
                self.transport.stop()
                for target in targets:
                    ensure_english_layout(target, self.stopped, verify)
                    self.notify(f"Раскладка «{target.title}»: ENG подтверждена")
            except Exception:
                self.stop()
                raise

    def _watch(self) -> None:
        while not self.stopped.wait(0.025):
            try:
                with self.lock:
                    self.sync_pause()
                    if not self.paused.is_set():
                        self.transport.heartbeat()
            except Exception as exc:
                logger.exception("Watchdog остановил ввод")
                self.error = str(exc)
                try:
                    self.stop()
                except Exception:
                    logger.exception("Ошибка STOP; ожидается watchdog платы")

    def sync_pause(self) -> None:
        # Вызывается под lock; ручная пауза не снимается возвращением фокуса.
        focused = True
        try:
            self.check()
        except FocusLost:
            focused = False
        should_pause = self.manual_paused.is_set() or not focused
        if should_pause and not self.paused.is_set():
            self.pause_generation += 1
            self.pause_started = time.monotonic()
            self.paused.set()
            self.transport.stop()
            message = "PAUSE — вручную" if self.manual_paused.is_set() else "Автопауза — окно не активно"
            logger.info(message)
            self.notify(message)
        elif not should_pause and self.paused.is_set():
            self.transport.arm()
            self.paused_seconds += time.monotonic() - self.pause_started
            self.pause_started = None
            self.paused.clear()
            logger.info("Сценарий продолжен; выбранное окно активно")
            self.notify("Продолжение — выбранное окно активно")

    def active_time(self) -> float:
        with self.lock:
            now = time.monotonic()
            pending = 0. if self.pause_started is None else now - self.pause_started
            return now - self.paused_seconds - pending

    def pause(self) -> None:
        with self.lock:
            if self.stopped.is_set():
                return
            self.manual_paused.set()
            self.sync_pause()

    def resume(self) -> None:
        with self.lock:
            self.manual_paused.clear()
            self.sync_pause()

    def checkpoint(self) -> None:
        while True:
            with self.lock:
                self.sync_pause()
                if not self.paused.is_set():
                    return
            self.stopped.wait(0.02)

    @contextmanager
    def ready(self) -> Iterator[None]:
        while True:
            self.checkpoint()
            self.lock.acquire()
            try:
                self.sync_pause()
                if not self.paused.is_set():
                    break
            except Exception:
                self.lock.release()
                raise
            self.lock.release()
        try:
            yield
        finally:
            self.lock.release()

    def capture(self) -> np.ndarray:
        # Потеря фокуса между двумя проверками capture не завершает сценарий.
        while True:
            self.checkpoint()
            try:
                frame = self.desktop.capture()
                self.check()
                return frame
            except FocusLost:
                continue

    def switch_window(self, target: Target, timeout: float = .75) -> None:
        """
        Переключает окно общей сессии только после освобождения HID.
        """
        if not 0 < timeout <= 1:
            raise ValueError("Лимит переключения должен быть от 0 до 1 секунды")
        with self.ready():
            if target == self.desktop.target:
                return
            try:
                current = snapshot(target.hwnd)
                if (current.pid, current.rect) != (target.pid, target.rect):
                    raise RuntimeError("Окно или его геометрия изменились")
                self.transport.stop()
                used_helper = False
                source = self.desktop.target
                route = (source.hwnd, source.pid, target.hwnd, target.pid)
                strategy = self.activation_strategies.get(route, "direct")

                def fallback() -> None:
                    self.check()
                    if self.manual_paused.is_set():
                        raise Stopped("Переключение отменено ручной паузой")
                    current = snapshot(target.hwnd)
                    if (current.pid, current.rect) != (target.pid, target.rect):
                        raise RuntimeError("Окно или его геометрия изменились")
                    message = f"Активация «{target.title}»: helper foreground"
                    logger.info(message)
                    self.notify(message)
                    try:
                        request_activation(
                            self.desktop.target, target, self.stopped,
                            lambda: self.hard_stop_ready() and not self.manual_paused.is_set(), timeout,
                        )
                        self.activation_strategies[route] = "foreground"
                    except RuntimeError as exc:
                        # Отмена и уход пользователя из игры не являются поводом
                        # перехватывать фокус ещё раз.
                        self.check()
                        if self.manual_paused.is_set():
                            raise Stopped("Переключение отменено ручной паузой") from exc
                        logger.warning("Foreground не сработал; восстанавливаем фокус без сброса COM: %s", exc)
                        try:
                            self.activation_strategies[route] = "attached_focus"
                            self._recover_focus(target, timeout)
                        except RuntimeError as reset_error:
                            raise RuntimeError(
                                f"Не удалось восстановить фокус «{target.title}»: {reset_error}"
                            ) from reset_error

                if strategy == "alt_tab":
                    self._alt_tab_activate(target)
                    used_helper = True
                elif strategy == "attached_focus":
                    self._recover_focus(target, timeout)
                    used_helper = True
                elif strategy == "foreground":
                    fallback()
                    used_helper = True
                else:
                    try:
                        activate(target)
                    except ActivationDenied:
                        logger.info("Прямой запрос фокуса отклонён: переключение через изолированный helper")
                        fallback()
                        used_helper = True
                destination = Desktop(target)
                deadline = time.monotonic() + timeout
                while True:
                    if self.stopped.is_set() or not self.hard_stop_ready():
                        raise Stopped("Переключение остановлено")
                    try:
                        destination.check()
                        break
                    except FocusLost:
                        if time.monotonic() >= deadline:
                            if used_helper:
                                raise RuntimeError("Не удалось подтвердить фокус второго окна")
                            fallback()
                            used_helper = True
                            deadline = time.monotonic() + timeout
                        self.stopped.wait(.01)
                self.desktop = destination
                self.check()
                if self.manual_paused.is_set():
                    self.sync_pause()
                else:
                    self.transport.arm()
                logger.info("Активировано окно %s [PID %s]", target.title, target.pid)
            except Exception:
                self.stop()
                raise

    def stop(self) -> None:
        self.stopped.set()
        with self.lock:
            self.transport.stop()

    def release_inputs(self) -> None:
        """
        Отпускает весь ввод перед ожиданием восстановления наблюдения.
        """
        self.checkpoint()
        with self.lock:
            self.transport.release()

    def _recover_focus(self, target: Target, timeout: float) -> None:
        """
        Восстанавливает foreground без переоткрытия исправного COM/HID.
        """
        self.check()
        if self.manual_paused.is_set():
            raise Stopped("Восстановление фокуса отменено ручной паузой")
        message = "Восстановление фокуса через attached_focus; COM/HID-сессия сохранена"
        logger.info(message)
        self.notify(message)
        self._ensure_alt_released()
        current = snapshot(target.hwnd)
        if (current.pid, current.rect) != (target.pid, target.rect):
            raise RuntimeError("Целевое окно изменилось при восстановлении фокуса")
        try:
            request_activation(
                self.desktop.target, target, self.stopped,
                lambda: self.hard_stop_ready() and not self.manual_paused.is_set(), timeout,
                strategy="attached_focus",
            )
        except RuntimeError:
            if self.transport.supports_alt_tab is not True:
                raise
            self._alt_tab_activate(target)
            source = self.desktop.target
            self.activation_strategies[(source.hwnd, source.pid, target.hwnd, target.pid)] = "alt_tab"

    def _alt_tab_activate(self, target: Target) -> None:
        """
        Последний резерв: Alt+Tab в disarmed, RELEASE и проверка перед ARM.
        """
        self.check()
        self._ensure_alt_released()
        current = snapshot(target.hwnd)
        if (current.pid, current.rect) != (target.pid, target.rect):
            raise RuntimeError("Целевое окно изменилось перед ALT+TAB")
        message = "Восстановление фокуса не помогло: резервный ALT+TAB с проверкой отпускания ALT"
        logger.warning(message)
        self.notify(message)
        self.transport.stop()
        try:
            self.transport.window_switch()
            # SWITCH подтверждает отправку, а не окончание 80 мс удержания.
            if self.stopped.wait(.08):
                raise Stopped("ALT+TAB отменён остановкой")
        finally:
            # Даже ошибка SWITCH или STOP обязаны вызвать попытку key-up.
            self.transport.release()
        self._ensure_alt_released()
        if self.stopped.wait(.2):
            raise Stopped("Остановка после ALT+TAB")
        # Повторная проверка после задержки исключает поздний ALT-down.
        self._ensure_alt_released()
        try:
            Desktop(target).check()
        except FocusLost as exc:
            raise RuntimeError("ALT+TAB не выбрал нужное окно; игровые нажатия заблокированы") from exc

    def close(self) -> None:
        self.stopped.set()
        if self.monitor is not None:
            self.monitor.join(timeout=1)
        with self.lock:
            self.transport.close()

    def _ensure_alt_released(self) -> None:
        """
        Подтверждает RELEASE на Pico и отпускание обоих ALT в Windows.
        """
        try:
            self.transport.release()
            deadline = time.monotonic() + .2
            while True:
                if self.stopped.is_set() or self.manual_paused.is_set() or not self.hard_stop_ready():
                    raise Stopped("Ввод остановлен при отпускании ALT")
                if not any(win32api.GetAsyncKeyState(key) & 0x8000 for key in (0x12, 0xA4, 0xA5)):
                    return
                if time.monotonic() >= deadline:
                    raise RuntimeError("ALT остаётся зажатым: игровые нажатия заблокированы")
                self.stopped.wait(.01)
        except Exception:
            self.stop()
            raise

    def _begin_hold(self, kind: str, name: str, duration: int) -> None:
        self._ensure_alt_released()
        self.check()
        self.transport.hold(kind, name, duration)

    def wait(self, seconds: float) -> None:
        deadline = self.active_time() + seconds
        while True:
            self.checkpoint()
            remaining = deadline - self.active_time()
            if remaining <= 0:
                return
            self.stopped.wait(min(0.02, remaining))

    def move(self, x: float, y: float, verify: Callable[[], None] | None = None,
             fast: bool = False, duration: float | None = None) -> None:
        if duration is not None and not 1 <= duration <= 2.5:
            raise ValueError("Возврат курсора должен занимать 1–2,5 секунды")
        if self.transport.protocol == "L2_PICO_V1":
            raise RuntimeError("Для движения мыши нужна прошивка HID_PILOT_V1; клавиши доступны")
        target = self.desktop.point(x, y)
        with self.ready():
            self.desktop.check_point(target)
            # После START курсор может быть над GUI. Входим в игру без зажатых кнопок.
            self.transport.release()
        if duration is not None:
            self._timed_return(target, verify, duration)
            return
        deadline = self.active_time() + 8
        points = mouse_path(self.desktop.cursor(), target, self.desktop.target.rect) if self.humanize and not fast else [target]
        interval = .001 if fast else (random.uniform(.001, .002) if self.humanize else .006)
        for point in points:
            self._move_to(point, verify, deadline, interval, fast)

    def _timed_return(self, target: tuple[int, int], verify: Callable[[], None] | None,
                      duration: float) -> None:
        started = self.active_time()
        points = mouse_path(self.desktop.cursor(), target, self.desktop.target.rect)
        gain = [2., 2.]
        previous = None
        steps = [0, 0]
        while self.active_time() - started < duration:
            elapsed = self.active_time() - started
            # При отставании пропускаем промежуточные точки, а не растягиваем всю дугу.
            index = min(len(points) - 1, int(elapsed / (duration * .75) * len(points)))
            with self.ready():
                if verify is not None:
                    verify()
                current = self.desktop.cursor()
                if previous is not None:
                    for axis in range(2):
                        actual = current[axis] - previous[axis]
                        if steps[axis] and actual * steps[axis] > 0:
                            gain[axis] = max(2., min(16., abs(actual / steps[axis])))
                point = points[index]
                self.desktop.check_point(point)
                delta = point[0] - current[0], point[1] - current[1]
                steps = [min(16, max(1, int(abs(value) / gain[axis]))) * (1 if value > 0 else -1)
                         if abs(value) > 2 else 0 for axis, value in enumerate(delta)]
                previous = current
                if any(steps):
                    self.transport.command(f"MOVE {steps[0]} {steps[1]}", "MOVED")
            self.wait(min(.006, max(0, duration - (self.active_time() - started))))
        # Возврат косметический: не продолжаем наведение сверх выбранного бюджета.
        if max(abs(a - b) for a, b in zip(self.desktop.cursor(), target)) > 2:
            logger.debug("Возврат курсора завершён по бюджету %.2f сек без достижения центра", duration)

    def _move_to(self, target: tuple[int, int], verify: Callable[[], None] | None, deadline: float,
                 interval: float = .006, fast: bool = False) -> None:
        if self.humanize or fast:
            self._feedback_move(target, verify, deadline, max(.003, interval))
            return
        while self.active_time() < deadline:
            # Три шага между паузами ускоряют дальний ход без превышения лимита Pico.
            for _ in range(3 if fast or not self.humanize else 1):
                with self.ready():
                    if verify is not None:
                        verify()
                    current = self.desktop.cursor()
                    self.desktop.check_point(target)
                    dx, dy = target[0] - current[0], target[1] - current[1]
                    if abs(dx) <= 2 and abs(dy) <= 2:
                        return
                    sx = max(-16, min(16, (dx if self.humanize or fast else dx // 2) if abs(dx) > 2 else 0))
                    sy = max(-16, min(16, (dy if self.humanize or fast else dy // 2) if abs(dy) > 2 else 0))
                    self.transport.command(f"MOVE {sx} {sy}", "MOVED")
            self.wait(interval)
        raise RuntimeError("HID-наведение не сошлось; клик отменён")

    def _feedback_move(self, target: tuple[int, int], verify: Callable[[], None] | None,
                       deadline: float, interval: float) -> None:
        gain = [2., 2.]
        previous: tuple[int, int] | None = None
        step = (0, 0)
        best = float("inf")
        stalled = 0
        unanswered = 0
        while self.active_time() < deadline:
            with self.ready():
                if verify is not None:
                    verify()
                current = self.desktop.cursor()
                self.desktop.check_point(target)
                error = (target[0] - current[0], target[1] - current[1])
                distance = max(abs(value) for value in error)
                if distance <= 2:
                    return
                if previous is not None:
                    for axis in range(2):
                        actual = current[axis] - previous[axis]
                        if step[axis] and actual * step[axis] > 0:
                            gain[axis] = max(1., min(16., abs(actual / step[axis])))
                if distance < best:
                    best, stalled = distance, 0
                else:
                    stalled += 1
                if stalled >= 20:
                    raise RuntimeError(f"Наведение не сходится; движение прекращено, клик отменён; "
                                       f"курсор={current}, цель={target}, остаток={error}, шаг={step}, усиление={gain}")
                # У цели уменьшаем шаг; учитываем фактическое ускорение Windows.
                values = []
                for axis, value in enumerate(error):
                    amount = max(1, int(abs(value) / max(2., gain[axis]))) if abs(value) > 2 else 0
                    values.append(min(16, amount) * (1 if value > 0 else -1))
                step = tuple(values)
                previous = current
                self.transport.command(f"MOVE {step[0]} {step[1]}", "MOVED")
            # ACK может прийти уже после движения: тогда дополнительная пауза не нужна.
            # По старой позиции следующую поправку не отправляем.
            response_until = min(deadline, self.active_time() + .06)
            polls = 0
            observed = previous
            while self.active_time() < response_until and polls < 20:
                with self.ready():
                    observed = self.desktop.cursor()
                if observed != previous:
                    break
                self.wait(interval)
                polls += 1
            unanswered = unanswered + 1 if observed == previous else 0
            if unanswered >= 3:
                raise RuntimeError(f"Наведение не сходится; движение прекращено, клик отменён; "
                                   f"Pico принял MOVE, но курсор не изменился; курсор={observed}, "
                                   f"цель={target}, последний шаг={step}")
        current = self.desktop.cursor()
        raise RuntimeError(f"HID-наведение не сошлось; клик отменён; курсор={current}, "
                           f"цель={target}, последний шаг={step}, усиление={gain}")

    def recover_view(self, deadline: float) -> None:
        """
        Ограниченное восстановление вида: WASD и три коротких поворота с ПКМ.
        """
        generation = self.pause_generation
        deadline = min(deadline, time.monotonic() + 7)

        def verify() -> None:
            self.check()
            if (self.paused.is_set() or self.pause_generation != generation
                    or time.monotonic() >= deadline):
                raise Stopped("Восстановление вида прервано паузой или лимитом времени")

        try:
            for key in ("W", "A", "S", "D"):
                with self.lock:
                    verify()
                    self._begin_hold("KEY", key, 350)
                self.wait(.37)
                with self.lock:
                    self.transport.release()
            self.move(.5, .5, verify=verify)
            for direction in (1, -1, 1):
                with self.lock:
                    verify()
                    self.desktop.check_point(self.desktop.cursor())
                    self._begin_hold("BUTTON", "right", 800)
                until = min(deadline, time.monotonic() + .6)
                while time.monotonic() < until:
                    with self.lock:
                        verify()
                        x, y = self.desktop.cursor()
                        self.desktop.check_point((x, y))
                        self.desktop.check_point((x + direction * 6, y))
                        self.transport.command(f"MOVE {direction * 6} 0", "MOVED")
                    self.wait(.02)
                with self.lock:
                    self.transport.release()
        finally:
            with self.lock:
                self.transport.release()

    def key(self, name: str, duration: int, *, exact: bool = False) -> None:
        if name not in KEYS or not 1 <= duration <= (3000 if self.transport.protocol == "HID_PILOT_V4" else 1000):
            raise ValueError("Недопустимая клавиша или длительность")
        if self.humanize and not exact and duration <= 200:
            duration = random.randint(max(1, round(duration * .7)), round(duration * 1.3))
        self._hold("KEY", name, duration)

    def target_by_name(self, name: str) -> None:
        """
        Выбирает цель через чат; требуется английская раскладка игры.
        """
        if self.transport.protocol != "HID_PILOT_V4":
            raise ValueError("Выбор через чат требует прошивки HID_PILOT_V4")
        if not name or len(name) > 80 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 '-" for char in name):
            raise ValueError("Имя цели содержит неподдерживаемые символы")
        self.key("ENTER", 80)
        self.wait(.15)
        self._hold("CHORD", "CTRL+A", 80)
        self.key("BACKSPACE", 80)
        for char in "/target " + name:
            if char.isupper():
                self._hold("CHORD", "SHIFT+" + char, 50)
            else:
                self.key({" ": "SPACE", "'": "APOSTROPHE"}.get(char, char.upper()), 50)
        self.key("ENTER", 80)
        self.wait(.15)
        current = self.desktop.cursor()
        left, top, right, bottom = self.desktop.target.rect
        if left <= current[0] < right and top <= current[1] < bottom:
            self.click_current("right")
        else:
            self.click("right", .5, .5)

    def click(self, button: str, x: float, y: float,
              verify: Callable[[], None] | None = None) -> None:
        if button not in {"left", "right"}:
            raise ValueError("Недопустимая кнопка мыши")
        self.move(x, y, verify=verify, fast=True)
        with self.ready():
            if verify is not None:
                verify()
            current = self.desktop.cursor()
            target = self.desktop.point(x, y)
            self.desktop.check_point(current)
            if max(abs(a - b) for a, b in zip(current, target)) > 2:
                raise RuntimeError("Курсор сместился; клик отменён")
            duration = random.randint(70, 130) if self.humanize else 80
            self._begin_hold("BUTTON", button, duration)
        self.wait(duration / 1000 + .02)
        with self.ready():
            self.transport.release()

    def _hold(self, kind: str, name: str, duration: int) -> None:
        with self.ready():
            self._begin_hold(kind, name, duration)
        self.wait(duration / 1000 + 0.02)
        with self.ready():
            self.transport.release()

    def click_current(self, button: str) -> None:
        if button not in {"left", "right"}:
            raise ValueError("Недопустимая кнопка мыши")
        with self.ready():
            self.desktop.check_point(self.desktop.cursor())
            duration = random.randint(70, 130) if self.humanize else 80
            self._begin_hold("BUTTON", button, duration)
        self.wait(duration / 1000 + .02)
        with self.ready():
            self.transport.release()
