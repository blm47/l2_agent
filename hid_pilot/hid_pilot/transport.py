import logging
import time

from hid_pilot.keyboard import KEYS, LEGACY_KEYS, V1_KEYS, V2_KEYS

logger = logging.getLogger("hid_pilot.transport")


class PicoTransport:
    """
    Синхронный канал, вызываемый только под lock ActionController.

    Ошибка блокирует соединение до перезапуска; команды не повторяются.
    """

    def __init__(self, port: str) -> None:
        import serial

        self.port = port
        self.sequence = 0
        self.failed = False
        self.last_heartbeat = 0.0
        self.serial = serial.Serial(port, 115200, timeout=0.1, write_timeout=0.1)
        try:
            # Сбрасываем прошлую сессию даже при быстром переоткрытии COM-порта.
            self.serial.dtr = False
            time.sleep(0.05)
            self.serial.dtr = True
            time.sleep(0.05)
            self.serial.reset_input_buffer()
            self.protocol = self.command("HELLO", ("HID_PILOT_V3", "HID_PILOT_V2", "HID_PILOT_V1", "L2_PICO_V1"))
        except Exception:
            self.serial.close()
            raise
        logger.info("Pico подключён: %s, протокол %s", port, self.protocol)

    def command(self, command: str, expected: str | tuple[str, ...]) -> str:
        if self.failed:
            raise OSError("Pico отключён после ошибки; перезапустите подключение")
        self.sequence += 1
        payload = f"{self.sequence} {command}\n".encode("ascii")
        try:
            if self.serial.write(payload) != len(payload):
                raise OSError("Pico: неполная запись команды")
            reply = self.serial.read_until(b"\n", 96)
            choices = (expected,) if isinstance(expected, str) else expected
            for choice in choices:
                if reply == f"{self.sequence} OK {choice}\n".encode("ascii"):
                    return choice
            raise OSError(f"Pico: неверный ответ или timeout: {reply!r}")
        except Exception:
            self.failed = True
            self.serial.close()
            logger.exception("Связь с Pico потеряна; ввод заблокирован")
            raise

    def arm(self) -> None:
        self.command("ARM", "ARMED")
        self.last_heartbeat = time.monotonic()

    def heartbeat(self) -> None:
        if time.monotonic() - self.last_heartbeat >= 0.1:
            self.command("HB", "ALIVE")
            self.last_heartbeat = time.monotonic()

    def hold(self, kind: str, name: str, duration_ms: int) -> None:
        if kind not in ("KEY", "BUTTON"):
            raise ValueError("Неизвестный тип команды Pico")
        if kind == "KEY":
            supported = {"L2_PICO_V1": LEGACY_KEYS, "HID_PILOT_V1": V1_KEYS,
                         "HID_PILOT_V2": V2_KEYS, "HID_PILOT_V3": KEYS}.get(self.protocol, ())
            if name not in KEYS:
                raise ValueError("Клавиша запрещена, включая hard-stop backslash")
            if name not in supported:
                required = "HID_PILOT_V1" if name == "=" else "HID_PILOT_V2" if name in V2_KEYS else "HID_PILOT_V3"
                raise ValueError(f"Клавиша {name} требует прошивки {required}")
        self.command(f"{kind} {name} {duration_ms}", "SENT")

    def release(self) -> None:
        self.command("RELEASE", "RELEASED")

    def stop(self) -> None:
        if not self.failed:
            self.command("STOP", "STOPPED")

    def close(self) -> None:
        try:
            self.stop()
        finally:
            self.serial.close()
