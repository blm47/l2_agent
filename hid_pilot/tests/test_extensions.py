import json
from unittest.mock import Mock

import pytest

from hid_pilot.extensions import extension_commands, extension_symbol, extensions_dir
from hid_pilot.runner import Runner
from hid_pilot.script import parse_script


def test_healer_and_farm_are_external_source_modules():
    for command in extension_commands().values():
        for reference in (command.config, command.prepare, command.run):
            symbol = extension_symbol(reference)
            assert symbol is not None
    assert not (extensions_dir().parent / "hid_pilot" / "healer.py").exists()
    assert not (extensions_dir().parent / "hid_pilot" / "farm.py").exists()


def test_new_external_command_is_loaded_validated_and_executed(monkeypatch, tmp_path):
    import hid_pilot

    package = tmp_path / "hid_pilot"
    package.mkdir()
    (package / "test_wait_extension.py").write_text(
        'from pydantic import BaseModel\n'
        'class Config(BaseModel):\n    seconds: float = .5\n'
        'def prepare(base, args):\n    pass\n'
        'def run(controller, base, args, config, notify, deadline):\n'
        '    controller.wait(config.seconds)\n', encoding="utf-8")
    (tmp_path / "commands.json").write_text(json.dumps({"api_version": 1, "commands": {
        "custom_wait": {"arguments": 0, "config": "hid_pilot.test_wait_extension:Config",
                        "prepare": "hid_pilot.test_wait_extension:prepare",
                        "run": "hid_pilot.test_wait_extension:run"}}}), encoding="utf-8")
    monkeypatch.setattr("hid_pilot.extensions.extensions_dir", lambda: tmp_path)
    monkeypatch.setattr(hid_pilot, "__path__", [*hid_pilot.__path__, str(package)])
    instructions = parse_script("set seconds 2\ncustom_wait")
    controller = Mock()
    Runner(controller, tmp_path, Mock(), {"seconds": "2"}).run(instructions)
    controller.wait.assert_called_once_with(2.)
    with pytest.raises(ValueError):
        parse_script("set seconds invalid\ncustom_wait")


def test_absent_extensions_leave_basic_scripts_available(monkeypatch, tmp_path):
    monkeypatch.setattr("hid_pilot.extensions.extensions_dir", lambda: tmp_path)
    assert parse_script("wait 1")[0].operation == "wait"
    with pytest.raises(ValueError):
        parse_script('healer "own.json" "party.json"')


def test_frozen_extensions_live_beside_executable(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", str(tmp_path / "HID_Pilot.exe"))
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path / "bundle"), raising=False)
    assert extensions_dir() == tmp_path / "extensions"


def test_missing_module_and_reserved_commands_are_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr("hid_pilot.extensions.extensions_dir", lambda: tmp_path)
    with pytest.raises(ValueError, match="не установлено"):
        extension_symbol("hid_pilot.missing:run")
    with pytest.raises(ValueError):
        extension_symbol("../escape:run")
    (tmp_path / "commands.json").write_text(json.dumps({"api_version": 1, "commands": {
        "key": {"arguments": 0, "config": "x", "prepare": "x", "run": "x"}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="Недопустимая"):
        extension_commands()
