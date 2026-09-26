# USB HID usage IDs совпадают с разрешёнными физическими клавишами агента.
KEYS = {
    "W": 26,
    "A": 4,
    "S": 22,
    "D": 7,
    "Q": 20,
    "E": 8,
    "R": 21,
    "I": 12,
    "M": 16,
    "SPACE": 44,
    "TAB": 43,
    "ESC": 41,
}
KEYS.update({key: 30 + index for index, key in enumerate("1234567890")})
KEYS.update({"F" + str(index): 57 + index for index in range(1, 9)})


class Control:
    """
    Одно удержание с независимым deadline и потерей допуска без heartbeat.
    """

    def __init__(self, keyboard, mouse, clock) -> None:
        self.keyboard = keyboard
        self.mouse = mouse
        self.clock = clock
        self.armed = False
        self.active = False
        self.deadline = 0.0
        self.heartbeat = 0.0
        self.sequence = -1
        self.buffer = bytearray()
        self.overflow = False
        self.release()

    def release(self) -> None:
        # Обе попытки нужны даже при ошибке первого USB report.
        try:
            self.keyboard.send_report(bytes(8))
        finally:
            self.mouse.send_report(bytes(4))
        self.active = False

    def disarm(self) -> None:
        self.armed = False
        self.release()

    def tick(self, connected: bool) -> None:
        now = self.clock()
        if not connected:
            self.buffer = bytearray()
            self.overflow = False
            self.sequence = -1
        if self.armed and (not connected or now - self.heartbeat >= 0.35):
            self.disarm()
        elif self.active and now >= self.deadline:
            self.release()

    def command(self, line: bytes) -> bytes:
        sequence = -1
        try:
            self.tick(True)
            fields = line.decode("ascii").split()
            sequence = int(fields[0])
            if sequence < 0 or sequence <= self.sequence:
                raise ValueError("SEQUENCE")
            self.sequence = sequence
            name = fields[1]
            args = fields[2:]
            if name == "HELLO" and not args:
                self.disarm()
                result = "L2_PICO_V1"
            elif name == "STOP" and not args:
                self.disarm()
                result = "STOPPED"
            elif name == "RELEASE" and not args:
                self.release()
                result = "RELEASED"
            elif name == "STATUS" and not args:
                result = "ARMED" if self.armed else "DISARMED"
            elif name == "ARM" and not args:
                self.disarm()
                self.armed = True
                self.heartbeat = self.clock()
                result = "ARMED"
            elif name == "HB" and not args and self.armed:
                self.heartbeat = self.clock()
                result = "ALIVE"
            elif name in ("KEY", "BUTTON") and len(args) == 2 and self.armed:
                duration = int(args[1])
                if not 1 <= duration <= 1000 or self.active:
                    raise ValueError("HOLD")
                if name == "KEY":
                    report = bytes((0, 0, KEYS[args[0]], 0, 0, 0, 0, 0))
                    device = self.keyboard
                else:
                    report = bytes(({"left": 1, "right": 2}[args[0]], 0, 0, 0))
                    device = self.mouse
                # Учёт начинается до USB-вызова, чтобы ошибка не скрыла удержание.
                self.active = True
                self.deadline = self.clock() + duration / 1000
                device.send_report(report)
                result = "SENT"
            else:
                raise ValueError("COMMAND")
            return (str(sequence) + " OK " + result + "\n").encode("ascii")
        except (ValueError, KeyError, IndexError, UnicodeError):
            self.disarm()
            return (str(sequence) + " ERR REJECTED\n").encode("ascii")

    def feed(self, data: bytes) -> list[bytes]:
        replies = []
        for byte in data:
            if byte == 10:
                if self.overflow:
                    self.disarm()
                    replies.append(b"-1 ERR LINE_TOO_LONG\n")
                else:
                    replies.append(self.command(bytes(self.buffer)))
                self.buffer = bytearray()
                self.overflow = False
            elif not self.overflow:
                if len(self.buffer) >= 64:
                    self.disarm()
                    self.buffer = bytearray()
                    self.overflow = True
                else:
                    self.buffer.append(byte)
        return replies
