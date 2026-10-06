import pytest

from hid_pilot.script import parse_script


def test_nested_blocks_and_windows_path():
    script = parse_script('repeat 2\nfind "C:\\images\\button.png" .95 3\n'
                          'if_found\nclick left\nend\nend')
    assert script[0].operation == "repeat"
    assert script[0].body[0].args[0] == r"C:\images\button.png"
    assert script[0].body[1].body[0].operation == "click"


@pytest.mark.parametrize("text", [
    "", "exec calc", "wait nan", "wait inf", "move -1 0", "click left 2 0",
    "key ALT+TAB 100", "key R 1001", "repeat 2\nwait 1", "end", "repeat 0\nend",
    "find a.png .1 3", "color 0 0 GGGGGG 0", "color 0 0 FF0000 256",
    "repeat 2\n" * 9 + "end\n" * 9,
])
def test_invalid_programs_rejected(text):
    with pytest.raises(ValueError):
        parse_script(text)


def test_all_keyboard_names_parse_and_hard_stop_does_not():
    from hid_pilot.script import KEYS
    for key in KEYS:
        assert parse_script(f"key {key} 100")[0].args == (key, "100")
    for key in ("\\", "BACKSLASH", "NONUSBACKSLASH"):
        with pytest.raises(ValueError):
            parse_script(f"key {key} 100")
