def response(command: bytes) -> bytes:
    """
    Диагностический протокол без отправки клавиш и движений мыши.
    """
    if command == b"PING":
        return b"PONG L2_PICO_DIAG_V1\n"
    if command == b"STATUS":
        return b"OK DIAGNOSTIC_ONLY INPUT_DISABLED\n"
    return b"ERR UNKNOWN_COMMAND\n"


class LineParser:
    """
    Ограничиваем буфер и отбрасываем слишком длинную строку целиком.
    """

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.overflow = False

    def feed(self, data: bytes) -> list[bytes]:
        replies = []
        for byte in data:
            if byte == 10:
                replies.append(
                    b"ERR LINE_TOO_LONG\n"
                    if self.overflow
                    else response(bytes(self.buffer).rstrip(b"\r"))
                )
                self.reset()
            elif not self.overflow:
                if len(self.buffer) >= 64:
                    self.buffer = bytearray()
                    self.overflow = True
                else:
                    self.buffer.append(byte)
        return replies

    def reset(self) -> None:
        self.buffer = bytearray()
        self.overflow = False
