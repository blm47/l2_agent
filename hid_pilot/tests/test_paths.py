
import pytest

from hid_pilot.paths import example_scripts_dir, resource_path, user_data_dir


def test_frozen_resources_are_read_inside_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
    assert resource_path("firmware/control.py") == tmp_path / "firmware" / "control.py"
    with pytest.raises(ValueError, match="границы"):
        resource_path("../external.py")


def test_user_scripts_are_writable_and_existing_changes_are_preserved(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    destination = example_scripts_dir()
    assert user_data_dir() == tmp_path / "HIDPilot"
    assert destination == user_data_dir() / "scripts"
    example = destination / "basic.pilot"
    assert example.exists()
    example.write_text("wait 10", "utf-8")
    assert example_scripts_dir() == destination
    assert example.read_text("utf-8") == "wait 10"
    assert not list(destination.glob("*.json"))


def test_scripts_are_not_extracted_from_exe_bundle(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user"))
    bundle = tmp_path / "bundle" / "examples"
    bundle.mkdir(parents=True)
    (bundle / "bundled.pilot").write_text("wait 1", encoding="utf-8")
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr("hid_pilot.extensions.extensions_dir", lambda: tmp_path / "absent")
    assert not list(example_scripts_dir().glob("*.pilot"))
