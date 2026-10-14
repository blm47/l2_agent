# USB HID Keyboard/Keypad: стандартная клавиатура, без hard-stop backslash.
KEYS = {key: 4 + index for index, key in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")}
KEYS.update({key: 30 + index for index, key in enumerate("1234567890")})
KEYS.update({"F" + str(index): 57 + index for index in range(1, 13)})
KEYS.update({"NUM" + str(index): 88 + index for index in range(1, 10)})
KEYS.update({
    "ENTER": 40, "ESC": 41, "BACKSPACE": 42, "TAB": 43, "SPACE": 44,
    "-": 45, "=": 46, "[": 47, "]": 48,
    ";": 51, "APOSTROPHE": 52, "GRAVE": 53, ",": 54, ".": 55, "/": 56,
    "CAPSLOCK": 57, "PRINTSCREEN": 70, "SCROLLLOCK": 71, "PAUSE": 72,
    "INSERT": 73, "HOME": 74, "PAGEUP": 75, "DELETE": 76, "END": 77,
    "PAGEDOWN": 78, "RIGHT": 79, "LEFT": 80, "DOWN": 81, "UP": 82,
    "NUMLOCK": 83, "NUMDIVIDE": 84, "NUMMULTIPLY": 85, "NUMSUBTRACT": 86,
    "NUMADD": 87, "NUMENTER": 88, "NUM0": 98, "NUMDECIMAL": 99, "MENU": 101,
    "LCTRL": 224, "LSHIFT": 225, "LALT": 226, "LWIN": 227,
    "RCTRL": 228, "RSHIFT": 229, "RALT": 230, "RWIN": 231,
    "CTRL": 224, "SHIFT": 225, "ALT": 226, "WIN": 227,
})


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
                result = "HID_PILOT_V4"
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
            elif name == "MOVE" and len(args) == 2 and self.armed and not self.active:
                dx, dy = int(args[0]), int(args[1])
                if not -16 <= dx <= 16 or not -16 <= dy <= 16:
                    raise ValueError("MOVE")
                self.mouse.send_report(bytes((0, dx & 255, dy & 255, 0)))
                result = "MOVED"
            elif name in ("KEY", "BUTTON", "CHORD") and len(args) == 2 and self.armed:
                duration = int(args[1])
                limit = 3000 if name == "KEY" else 1000
                if not 1 <= duration <= limit or self.active:
                    raise ValueError("HOLD")
                if name == "CHORD":
                    # Разрешены только очистка чата и Shift для печати символов.
                    modifier, key = args[0].split("+")
                    if not (modifier == "CTRL" and key == "A" or
                            modifier == "SHIFT" and key in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
                        raise ValueError("CHORD")
                    report = bytes((1 if modifier == "CTRL" else 2, 0, KEYS[key], 0, 0, 0, 0, 0))
                    device = self.keyboard
                elif name == "KEY":
                    usage = KEYS[args[0]]
                    # Модификатор занимает бит первого байта, а не слот обычной клавиши.
                    modifier = 1 << (usage - 224) if usage >= 224 else 0
                    report = bytes((modifier, 0, 0 if modifier else usage, 0, 0, 0, 0, 0))
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
