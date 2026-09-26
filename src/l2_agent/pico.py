import logging
import time

logger = logging.getLogger("l2_agent.pico")


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
            self.command("HELLO", "L2_PICO_V1")
        except Exception:
            self.serial.close()
            raise
        logger.info("Pico подключён: %s, протокол L2_PICO_V1", port)

    def command(self, command: str, expected: str) -> None:
        if self.failed:
            raise OSError("Pico отключён после ошибки; перезапустите подключение")
        self.sequence += 1
        payload = f"{self.sequence} {command}\n".encode("ascii")
        try:
            if self.serial.write(payload) != len(payload):
                raise OSError("Pico: неполная запись команды")
            reply = self.serial.read_until(b"\n", 96)
            if reply != f"{self.sequence} OK {expected}\n".encode("ascii"):
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
