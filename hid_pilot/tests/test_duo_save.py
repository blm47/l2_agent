import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from hid_pilot.duo import DuoConfig
from hid_pilot.duo_panel import DuoPanel


@pytest.mark.parametrize("suffix", [".pilot", ".json"])
def test_save_copy_preserves_profiles_and_existing_destination(tmp_path, monkeypatch, suffix):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.duo.pilot"
    source_base = tmp_path / "source.duo"
    target = tmp_path / "copies" / ("copy.duo" + suffix)
    target.parent.mkdir()
    for role in ("farmer", "support"):
        directory = source_base / role
        directory.mkdir(parents=True)
        (directory / "farm-bars.json").write_text(role, encoding="utf-8")
        (directory / "party.json").write_text("party", encoding="utf-8")
    existing = target.parent / target.stem / "farmer" / "farm-bars.json"
    existing.parent.mkdir(parents=True)
    existing.write_text("custom", encoding="utf-8")
    monkeypatch.setattr("hid_pilot.duo_panel.QFileDialog.getSaveFileName",
                        lambda *args: (str(target), ""))
    notify = Mock()
    panel = DuoPanel(lambda: None, lambda: False, notify)
    try:
        panel.select_script(source, DuoConfig())
        panel.save_script()
        notify.assert_not_called()
        assert panel.path == target
        assert target.is_file()
        assert existing.read_text("utf-8") == "custom"
        assert (panel.base() / "support" / "farm-bars.json").read_text("utf-8") == "support"
        assert (panel.base() / "farmer" / "party.json").read_text("utf-8") == "party"
        assert (source_base / "farmer" / "farm-bars.json").read_text("utf-8") == "farmer"
    finally:
        panel.close()
