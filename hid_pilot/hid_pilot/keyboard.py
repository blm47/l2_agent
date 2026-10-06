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


# Наборы старых протоколов нужны для отказа до отправки несовместимой команды.
LEGACY_KEYS = frozenset("WASDQERIM1234567890") | {"SPACE", "TAB", "ESC"} | {f"F{i}" for i in range(1, 9)}
V1_KEYS = LEGACY_KEYS | {"="}
V2_KEYS = V1_KEYS | {"-", "NUM7"}
