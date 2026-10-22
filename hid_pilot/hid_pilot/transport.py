import logging
import threading
import time

from hid_pilot.keyboard import KEYS, LEGACY_KEYS, V1_KEYS, V2_KEYS

logger = logging.getLogger("hid_pilot.transport")


class PicoTransport:
    """
    Синхронный канал, вызываемый только под lock ActionController.

    Ошибка блокирует соединение до перезапуска; команды не повторяются.
    """

    def __init__(self, port: str) -> None:
        self.port = port
        self.lock = threading.RLock()
        self._open_session()

    def _open_session(self) -> None:
        import serial

        self.sequence = 0
        self.failed = False
        self.last_heartbeat = 0.0
        self.serial = serial.Serial(self.port, 115200, timeout=0.1, write_timeout=0.1)
        try:
            # Сбрасываем прошлую сессию даже при быстром переоткрытии COM-порта.
            self.serial.dtr = False
            time.sleep(0.05)
            self.serial.dtr = True
            time.sleep(0.05)
            self.serial.reset_input_buffer()
            self.firmware_protocol = self.command("HELLO", ("HID_PILOT_V5", "HID_PILOT_V4", "HID_PILOT_V3", "HID_PILOT_V2", "HID_PILOT_V1", "L2_PICO_V1"))
            self.supports_alt_tab = self.firmware_protocol == "HID_PILOT_V5"
            # V5 сохраняет набор обычных команд V4; SWITCH доступен отдельно.
            self.protocol = "HID_PILOT_V4" if self.supports_alt_tab else self.firmware_protocol
        except Exception:
            self.serial.close()
            raise
        logger.info("Pico подключён: %s, прошивка %s", self.port, self.firmware_protocol)

    def reset_session(self) -> None:
        with self.lock:
            try:
                self.stop()
            finally:
                self.serial.close()
            try:
                self._open_session()
                self.command("STATUS", "DISARMED")
            except Exception:
                self.failed = True
                self.serial.close()
                raise
            logger.info("COM/HID-сессия Pico сброшена: %s; ввод disarmed", self.port)

    def command(self, command: str, expected: str | tuple[str, ...]) -> str:
        with self.lock:
            return self._command(command, expected)

    def _command(self, command: str, expected: str | tuple[str, ...]) -> str:
        if self.failed:
            raise OSError("Pico отключён после ошибки; перезапустите подключение")
        self.sequence += 1
        payload = f"{self.sequence} {command}\n".encode("ascii")
        try:
            if self.serial.write(payload) != len(payload):
                raise OSError("Pico: неполная запись команды")
            # CDC может вернуть часть строки при timeout: дочитываем тот же ответ,
            # не повторяя команду ввода и не принимая ответ другой sequence.
            deadline = time.monotonic() + .25
            reply = bytearray()
            while len(reply) < 96 and time.monotonic() < deadline:
                chunk = self.serial.read_until(b"\n", 96 - len(reply))
                reply.extend(chunk)
                if reply.endswith(b"\n"):
                    break
            reply = bytes(reply)
            choices = (expected,) if isinstance(expected, str) else expected
            for choice in choices:
                if reply == f"{self.sequence} OK {choice}\n".encode("ascii"):
                    return choice
            raise OSError(f"Pico: команда {command}: неверный ответ или timeout: {reply!r}")
        except Exception:
            self.failed = True
            self.serial.close()
            logger.exception("Связь с Pico потеряна; ввод заблокирован")
            raise

    def arm(self) -> None:
        self.command("ARM", "ARMED")
        self.last_heartbeat = time.monotonic()

    def heartbeat(self) -> None:
        started = time.monotonic()
        if started - self.last_heartbeat >= 0.1:
            self.command("HB", "ALIVE")
            self.last_heartbeat = started

    def hold(self, kind: str, name: str, duration_ms: int) -> None:
        if kind not in ("KEY", "BUTTON", "CHORD"):
            raise ValueError("Неизвестный тип команды Pico")
        if not 1 <= duration_ms <= (3000 if kind == "KEY" and self.protocol == "HID_PILOT_V4" else 1000):
            raise ValueError("Недопустимая длительность удержания Pico")
        if kind == "CHORD":
            if self.protocol != "HID_PILOT_V4":
                raise ValueError("Ввод в чат требует прошивки HID_PILOT_V4")
            if name != "CTRL+A" and name not in {"SHIFT+" + key for key in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}:
                raise ValueError("Сочетание клавиш запрещено")
        if kind == "KEY":
            if name in {"ALT", "LALT", "RALT"}:
                raise ValueError("ALT разрешён только в резервном переключении окон")
            supported = {"L2_PICO_V1": LEGACY_KEYS, "HID_PILOT_V1": V1_KEYS,
                         "HID_PILOT_V2": V2_KEYS, "HID_PILOT_V3": KEYS,
                         "HID_PILOT_V4": KEYS}.get(self.protocol, ())
            if name not in KEYS:
                raise ValueError("Клавиша запрещена, включая hard-stop backslash")
            if name not in supported:
                required = "HID_PILOT_V1" if name == "=" else "HID_PILOT_V2" if name in V2_KEYS else "HID_PILOT_V3"
                raise ValueError(f"Клавиша {name} требует прошивки {required}")
        self.command(f"{kind} {name} {duration_ms}", "SENT")

    def release(self) -> None:
        self.command("RELEASE", "RELEASED")

    def window_switch(self) -> None:
        if not self.supports_alt_tab:
            raise ValueError("ALT+TAB требует обновления Pico до HID_PILOT_V5")
        self.command("SWITCH ALT+TAB 80", "SENT")

    def stop(self) -> None:
        if not self.failed:
            self.command("STOP", "STOPPED")

    def close(self) -> None:
        try:
            self.stop()
        finally:
            self.serial.close()
