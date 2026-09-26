from firmware.pico.diagnostics import LineParser


def test_fragmented_and_combined_commands():
    parser = LineParser()
    assert parser.feed(b"PI") == []
    assert parser.feed(b"NG\r\nSTATUS\n") == [
        b"PONG L2_PICO_DIAG_V1\n",
        b"OK DIAGNOSTIC_ONLY INPUT_DISABLED\n",
    ]


def test_overflow_cannot_become_valid_command():
    parser = LineParser()
    assert parser.feed(b"x" * 10000) == []
    assert len(parser.buffer) <= 64
    assert parser.feed(b"PING\nPING\n") == [
        b"ERR LINE_TOO_LONG\n",
        b"PONG L2_PICO_DIAG_V1\n",
    ]


def test_disconnect_discards_partial_command():
    parser = LineParser()
    parser.feed(b"PI")
    parser.reset()
    assert parser.feed(b"NG\n") == [b"ERR UNKNOWN_COMMAND\n"]


def test_input_commands_are_not_supported():
    assert LineParser().feed(b"KEY W\n") == [b"ERR UNKNOWN_COMMAND\n"]
