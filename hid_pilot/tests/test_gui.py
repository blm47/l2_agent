import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from hid_pilot.__main__ import MainWindow
from hid_pilot.auto_numbers import NumberCandidate
from hid_pilot.auto_review import AutoReview
from hid_pilot.bars import BarBox
from hid_pilot.desktop import Target
from hid_pilot.numbers import parse_numbers
from hid_pilot.picker import ScreenPicker


def test_editor_validation_without_hid(monkeypatch):
    app = QApplication.instance() or QApplication([])
    hotkey = Mock(available=True)
    hotkey_factory = Mock(return_value=hotkey)
    monkeypatch.setattr("hid_pilot.__main__.HardStopHotkey", hotkey_factory)
    monkeypatch.setattr("hid_pilot.__main__.windows", lambda: [
        Target(hwnd=123, pid=42, title="Тестовое окно", rect=(0, 0, 800, 600))
    ])
    transport = Mock()
    monkeypatch.setattr("hid_pilot.__main__.PicoTransport", transport)
    window = MainWindow()
    try:
        window.show()
        app.processEvents()
        assert window.tabs.tabText(0) == "Общее"
        from PySide6.QtWidgets import QPushButton
        labels = {button.text() for button in window.findChildren(QPushButton)}
        assert "PAUSE / RESUME" in labels
        assert not any("F9" in label for label in labels)
        hotkey_factory.assert_called_once()
        assert not labels.intersection({"Моё HP", "Моё MP", "HP цели", "Цифры HP", "Цифры MP"})
        window.tabs.setCurrentIndex(1)
        app.processEvents()
        assert window.editor.height() >= 300
        assert window.editor.lineWrapMode() == window.editor.LineWrapMode.NoWrap
        assert len(window.quick.rows) == 4
        requested = []
        window.quick.requested.disconnect()
        window.quick.requested.connect(requested.append)
        window.quick.rows[1][0].setChecked(True)
        window.quick.start_selected()
        assert [plan.key for plan in requested[0]] == ["R", "1"]
        window.quick.set_running(requested[0])
        window.quick.start_row(0)
        assert window.quick.cancelled[0].is_set()
        assert not window.quick.cancelled[1].is_set()
        window.quick.set_running(None)
        window.editor.setPlainText("repeat 2\nwait .1\nend")
        window.validate()
        assert "проверен" in window.status.text()
        window.editor.setPlainText("key BACKSLASH 100")
        window.validate()
        assert "Строка 1" in window.status.text()
        transport.assert_not_called()
    finally:
        window.close()


def test_script_party_panel_duplicates_live_state_without_journal_messages(monkeypatch):
    from test_healer import snapshot

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("hid_pilot.__main__.HardStopHotkey", lambda *a, **k: Mock(available=True))
    monkeypatch.setattr("hid_pilot.__main__.windows", list)
    window = MainWindow()
    try:
        state = snapshot()
        before = window.journal.toPlainText()
        window.show_observation({"state": state, "reason": "Наблюдение"})
        assert "CP 100%" in window.party_state.toPlainText()
        assert " 1 | 100% | 100% | 100%" in window.party_state.toPlainText()
        assert window.party.status.text() == window.party_state.toPlainText()
        assert window.journal.toPlainText() == before
        window.show_observation({"state": snapshot(party=(.5, 1.)), "reason": "Лечение"})
        assert " 1 | 100% |  50% | 100%" in window.party_state.toPlainText()
        assert window.party_state.toPlainText().count("ПАТИ — участников") == 1
        assert window.log_level.currentText() == "INFO"
        app.processEvents()
    finally:
        window.close()


def test_auto_review_suggests_player_region_and_keeps_manual_correction():
    from hid_pilot.auto_player import PlayerCandidate

    app = QApplication.instance() or QApplication([])
    box = BarBox(x1=.4, x2=.6, y1=.01, y2=.1)
    dialog = AutoReview(np.zeros((800, 1200, 3), dtype=np.uint8), [], None,
                        player_candidates=[PlayerCandidate(box=box, evidence="originalNutta / Hawkeye / AES")])
    try:
        assert dialog.target_player_panel == box
        assert dialog.canvas.boxes["Игрок: ник / класс / клан"] == box
        assert dialog.player_edit.isEnabled()
        dialog.player_choice.setCurrentIndex(0)
        assert dialog.target_player_panel is None
        app.processEvents()
    finally:
        dialog.close()


def test_script_journal_can_be_expanded(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("hid_pilot.__main__.HardStopHotkey", lambda _: Mock(available=True))
    monkeypatch.setattr("hid_pilot.__main__.windows", lambda: [])
    window = MainWindow()
    try:
        window.resize(1000, 1200)
        window.tabs.setCurrentIndex(1)
        window.show()
        window.show_status("Скрипт выполняется")
        app.processEvents()
        initial_height = window.journal.height()
        handle = window.log_splitter.handle(1)
        position = handle.rect().center()
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=position)
        QTest.mouseMove(handle, position - QPoint(0, 150))
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert window.journal.height() > initial_height
        assert window.journal.height() > 90
        assert "Скрипт выполняется" in window.journal.toPlainText()
        assert window.journal.isReadOnly()
        window.show_status("Своя строка\nУчастник 1 │ Участник 2")
        app.processEvents()
        assert window.status.text() == "Своя строка"
        assert "Участник 1 │ Участник 2" in window.journal.toPlainText()
        assert window.journal.lineWrapMode() == window.journal.LineWrapMode.NoWrap
        assert window.start_button.isVisible()
    finally:
        window.close()


def test_picker_click_inserts_source_pixel_without_game_input():
    app = QApplication.instance() or QApplication([])
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    image[50, 100] = (10, 20, 30)
    picker = ScreenPicker(image, (0, 0, 200, 100))
    picker.show()
    app.processEvents()
    QTest.mouseClick(picker, Qt.MouseButton.LeftButton, pos=QPoint(100, 50))
    assert picker.command.endswith("1E140A 15")
    assert picker.result() == picker.DialogCode.Accepted
    picker.close()


def test_picker_drag_returns_normalized_full_roi():
    app = QApplication.instance() or QApplication([])
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    picker = ScreenPicker(image, (0, 0, 200, 100), "hp")
    picker.show()
    app.processEvents()
    QTest.mousePress(picker, Qt.MouseButton.LeftButton, pos=QPoint(10, 50))
    QTest.mouseRelease(picker, Qt.MouseButton.LeftButton, pos=QPoint(110, 59))
    assert picker.box.x1 == .05
    assert picker.box.x2 == .555
    assert picker.box.y2 == .6
    picker.close()


def test_auto_review_suggestions_without_manual_drawing():
    app = QApplication.instance() or QApplication([])
    image = np.zeros((200, 400, 3), dtype=np.uint8)
    parent = QApplication.activeWindow()
    candidate = NumberCandidate(box=BarBox(x1=.1, y1=.1, x2=.3, y2=.2),
                                numbers=parse_numbers("129/129", .99), kind="hp",
                                evidence="HP")
    dialog = AutoReview(image, [candidate], parent)
    dialog.show()
    app.processEvents()
    assert dialog.boxes == {"hp": candidate.box}
    assert "cp" in dialog.choices
    area = dialog.canvas.viewport()
    begin = QPoint(round(area.left() + area.width() * .1), round(area.top() + area.height() * .3))
    end = QPoint(round(area.left() + area.width() * .3), round(area.top() + area.height() * .4))
    QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=begin)
    QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end)
    assert dialog.boxes == {"hp": candidate.box}
    dialog.choices["hp"].setCurrentIndex(0)
    assert "hp" not in dialog.boxes
    dialog.reject()


def test_auto_review_cp_party_overlay_and_step_validation():
    from hid_pilot.auto_party import PartyCandidate
    from hid_pilot.party import PartyProfile

    app = QApplication.instance() or QApplication([])
    regions = {key: BarBox(x1=.1, x2=.9, y1=y, y2=y + .01)
               for key, y in (("name", 0), ("cp", .04), ("hp", .05), ("mp", .06))}
    party = PartyProfile(width=200, height=800, row_step=80, **regions)
    cp = NumberCandidate(box=regions["cp"], numbers=parse_numbers("88/88", .99),
                         kind="cp", evidence="CP")
    dialog = AutoReview(np.zeros((800, 200, 3), dtype=np.uint8), [cp], None,
                        [PartyCandidate(profile=party, evidence="Member")])
    app.processEvents()
    assert dialog.boxes == {"cp": cp.box}
    assert dialog.party_profile() == party
    assert len(dialog.canvas.boxes) == 34
    dialog.party_step.setValue(500)
    dialog.save()
    assert dialog.result() != dialog.DialogCode.Accepted
    assert dialog.party_error.text()
    dialog.party_step.setValue(80)
    dialog.save()
    assert dialog.result() == dialog.DialogCode.Accepted
    dialog.close()


def test_party_panel_discards_disabled_and_stale_results(tmp_path):
    import time

    from hid_pilot.desktop import Target
    from hid_pilot.party_panel import PartyPanel
    app = QApplication.instance() or QApplication([])
    target = Target(hwnd=1, pid=2, title="test", rect=(0, 0, 200, 800))
    panel = PartyPanel(lambda: target, lambda: tmp_path / "farm.pilot", lambda: False)
    app.processEvents()
    panel.enabled.setChecked(True)
    payload = {"generation": panel.generation, "started": time.monotonic() - 4,
               "target": target, "path": tmp_path / "party.json", "text": "old member"}
    panel.receive(payload)
    assert "устарел" in panel.status.text()
    payload["started"] = time.monotonic()
    panel.receive(payload)
    assert panel.status.text() == "old member"
    panel.enabled.setChecked(False)
    panel.receive(payload)
    assert "выключено" in panel.status.text()
    assert panel.shutdown()
    panel.close()


def test_combined_review_save_and_cancel_preserve_correct_profiles(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QDialog

    from hid_pilot.bars import BarProfile
    from hid_pilot.party import PartyProfile

    app = QApplication.instance() or QApplication([])
    target = Target(hwnd=123, pid=42, title="Test", rect=(0, 0, 200, 800))
    monkeypatch.setattr("hid_pilot.__main__.HardStopHotkey", lambda *a, **k: Mock(available=True))
    monkeypatch.setattr("hid_pilot.__main__.windows", lambda: [target])
    monkeypatch.setattr("hid_pilot.__main__.snapshot", lambda hwnd: target)
    box = BarBox(x1=.1, x2=.9, y1=.1, y2=.11)
    party = PartyProfile(width=200, height=800, row_step=80,
                         name=box, cp=box, hp=box, mp=box)
    bar_path = tmp_path / "farm-bars.json"
    party_path = tmp_path / "party.json"
    bar_path.write_text(BarProfile(width=200, height=800, bars={"mp": box}).model_dump_json(), "utf-8")
    party_path.write_text(party.model_dump_json(), "utf-8")
    before = (bar_path.read_bytes(), party_path.read_bytes())
    dialog = Mock(boxes={"cp": box}, target_player_panel=box)
    dialog.exec.return_value = QDialog.DialogCode.Rejected
    dialog.party_profile.return_value = party
    monkeypatch.setattr("hid_pilot.auto_review.AutoReview", lambda *args: dialog)
    window = MainWindow()
    window.path = tmp_path / "farm.pilot"
    payload = {"generation": window.auto_generation, "target": target, "path": window.path,
               "image": np.zeros((800, 200, 3), dtype=np.uint8), "candidates": []}
    try:
        app.processEvents()
        window.review_auto_numbers(payload)
        assert (bar_path.read_bytes(), party_path.read_bytes()) == before
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        window.review_auto_numbers(payload)
        saved = BarProfile.model_validate_json(bar_path.read_text("utf-8"))
        assert saved.target_player_panel == box
        assert saved.numbers == {"cp": box}
        assert saved.bars == {}
        assert PartyProfile.model_validate_json(party_path.read_text("utf-8")) == party
        dialog.party_profile.return_value = None
        window.review_auto_numbers(payload)
        assert not party_path.exists()
    finally:
        window.close()


def test_manual_party_draws_template_and_accounts_for_two_pets():
    from hid_pilot.party import PartyProfile
    from hid_pilot.party_review import PartyReview

    app = QApplication.instance() or QApplication([])
    dialog = PartyReview(np.zeros((1000, 600, 3), dtype=np.uint8), None)
    dialog.show()
    app.processEvents()
    for kind, y1, y2 in (("name", .03, .05), ("cp", .06, .065),
                         ("hp", .067, .077), ("mp", .079, .089)):
        dialog.field.setCurrentIndex(dialog.field.findData(kind))
        area = dialog.canvas.viewport()
        begin = QPoint(round(area.left() + .05 * area.width()), round(area.top() + y1 * area.height()))
        end = QPoint(round(area.left() + .4 * area.width()), round(area.top() + y2 * area.height()))
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=begin)
        QTest.mouseMove(dialog.canvas, end)
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end)
    dialog.step.setValue(80)
    dialog.gaps[3].setValue(20)
    dialog.gaps[6].setValue(20)
    profile = dialog.profile()
    assert profile.row_offsets == [0, 80, 160, 240, 340, 420, 500, 600]
    assert len(dialog.canvas.boxes) == 32
    dialog.zoom_party()
    view = dialog.canvas.view_box
    area = dialog.canvas.viewport()
    middle = dialog.canvas.normalized(area.center())
    assert middle.x() == pytest.approx((view.x1 + view.x2) / 2)
    assert middle.y() == pytest.approx((view.y1 + view.y2) / 2)
    assert view.x2 < 1
    dialog.save()
    assert dialog.result() == dialog.DialogCode.Accepted
    saved = PartyProfile.model_validate_json(profile.model_dump_json())
    reopened = PartyReview(dialog.image, saved)
    assert reopened.profile() == saved
    reopened.reject()
    dialog.close()


def test_manual_party_selection_and_invalid_spacing():
    from hid_pilot.party_review import PartyReview

    app = QApplication.instance() or QApplication([])
    image = np.zeros((800, 600, 3), dtype=np.uint8)
    for slot in range(8):
        y = 60 + slot * 80
        image[y:y + 5, 30:230] = (0, 150, 220)
        image[y + 7:y + 17, 25:180] = (0, 0, 180)
        image[y + 19:y + 29, 20:90] = (180, 0, 0)
    dialog = PartyReview(image, None)
    app.processEvents()
    dialog.select_region(BarBox(x1=0, x2=.45, y1=.02, y2=.9))
    assert dialog.profile().cp.y1 == 60 / 800
    assert len(dialog.canvas.boxes) == 32
    dialog.gaps[3].setValue(400)
    dialog.save()
    assert dialog.result() != dialog.DialogCode.Accepted
    assert "границы" in dialog.status.text()
    dialog.reject()


def test_manual_party_save_cancel_and_changed_geometry(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QDialog

    from hid_pilot.party import PartyProfile

    app = QApplication.instance() or QApplication([])
    target = Target(hwnd=123, pid=42, title="Test", rect=(0, 0, 200, 800))
    monkeypatch.setattr("hid_pilot.__main__.HardStopHotkey", lambda *a, **k: Mock(available=True))
    monkeypatch.setattr("hid_pilot.__main__.windows", lambda: [target])
    current = Mock(return_value=target)
    monkeypatch.setattr("hid_pilot.__main__.snapshot", current)
    regions = {key: BarBox(x1=.1, x2=.9, y1=y, y2=y + .01)
               for key, y in (("name", 0), ("cp", .04), ("hp", .05), ("mp", .06))}
    original = PartyProfile(width=200, height=800, row_step=80, **regions)
    changed = PartyProfile.model_validate(original.model_dump() | {
        "row_offsets": [0, 80, 160, 240, 340, 420, 500, 600]})
    path = tmp_path / "party.json"
    path.write_text(original.model_dump_json(), "utf-8")
    own = tmp_path / "farm-bars.json"
    own.write_bytes(b"unchanged")
    dialog = Mock()
    dialog.exec.return_value = QDialog.DialogCode.Rejected
    dialog.profile.return_value = changed
    factory = Mock(return_value=dialog)
    monkeypatch.setattr("hid_pilot.party_review.PartyReview", factory)
    window = MainWindow()
    window.path = tmp_path / "farm.pilot"
    app.processEvents()
    payload = {"generation": window.auto_generation, "target": target, "path": window.path,
               "image": np.zeros((800, 200, 3), dtype=np.uint8)}
    try:
        window.review_party(payload)
        assert PartyProfile.model_validate_json(path.read_text("utf-8")) == original
        assert factory.call_args.args[1] == original
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        current.side_effect = [target, target.model_copy(update={"rect": (0, 0, 300, 800)})]
        window.review_party(payload)
        assert PartyProfile.model_validate_json(path.read_text("utf-8")) == original
        current.side_effect = None
        window.review_party(payload)
        assert PartyProfile.model_validate_json(path.read_text("utf-8")) == changed
        assert own.read_bytes() == b"unchanged"
    finally:
        window.close()


def test_firmware_menu_blocks_game_execution_and_uses_checked_port(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("hid_pilot.__main__.HardStopHotkey", lambda *a, **k: Mock(available=True))
    monkeypatch.setattr("hid_pilot.__main__.windows", lambda: [])
    transport = Mock()
    monkeypatch.setattr("hid_pilot.__main__.PicoTransport", transport)
    window = MainWindow()
    factory = Mock()
    monkeypatch.setattr("hid_pilot.firmware_panel.FirmwareDialog", factory)

    def create(available, port, parent):
        assert available()
        assert parent is window
        assert window.firmware_open
        window.launch()
        transport.assert_not_called()
        return Mock(verified_port="COM77")

    factory.side_effect = create
    try:
        app.processEvents()
        window.worker = Mock()
        window.worker.is_alive.return_value = True
        window.open_firmware()
        factory.assert_not_called()
        window.worker = None
        window.open_firmware()
        assert window.port.text() == "COM77"
        assert not window.firmware_open
        assert window.start_button.isEnabled()
    finally:
        window.close()
