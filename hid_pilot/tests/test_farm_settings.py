import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QPlainTextEdit, QScrollArea

from hid_pilot.duo import DuoConfig, format_duo_settings
from hid_pilot.farm_settings import FarmSettings, setting_description


@pytest.fixture
def widgets():
    app = QApplication.instance() or QApplication([])
    editor = QPlainTextEdit('# Мой сценарий\nset rest "="\nfarm_melee "farm-bars.json"\n')
    settings = FarmSettings(editor)
    yield editor, settings, app
    settings.close()
    editor.close()


def test_groups_include_buffs_vampiric_and_separate_potion(widgets):
    _, settings, _ = widgets
    titles = [settings.groups.itemText(index) for index in range(settings.groups.count())]
    assert "МА — селф и пати-баффы" in titles
    assert "МА — вампирик" in titles
    assert "МА — зелье" in titles


def test_editing_field_preserves_other_settings_comments_and_command(widgets):
    editor, settings, _ = widgets
    settings.fields["vampiric_key"].setText("F8")
    settings.apply_field("vampiric_key")
    assert settings.read_config().vampiric_key == "F8"
    assert '# Мой сценарий\nset rest "="\nfarm_melee "farm-bars.json"\n' in editor.toPlainText()
    settings.fields["buff1_cast_seconds"].setText("2.5")
    settings.apply_field("buff1_cast_seconds")
    assert settings.read_config().buff1_cast_seconds == 2.5
    settings.fields["vampiric_key"].setText("")
    settings.apply_field("vampiric_key")
    assert settings.read_config().vampiric_key is None


def test_invalid_gui_value_does_not_change_script(widgets):
    editor, settings, _ = widgets
    before = editor.toPlainText()
    settings.fields["vampiric_hp_threshold"].setText("75")
    settings.apply_field("vampiric_hp_threshold")
    assert editor.toPlainText() == before
    assert settings.status.text()


def test_text_changes_refresh_form(widgets):
    editor, settings, _ = widgets
    editor.setPlainText('set rest "="\nset buff2_key F6\nfarm_melee "farm-bars.json"\n')
    assert settings.fields["buff2_key"].text() == '"F6"'


def test_duo_groups_and_legacy_json_editing(widgets):
    editor, _, _ = widgets
    editor.setPlainText(DuoConfig(heal_key="F9").model_dump_json())
    settings = FarmSettings(editor, duo=True)
    titles = [settings.groups.itemText(index) for index in range(settings.groups.count())]
    assert "Хиллер — пати-хил" in titles
    settings.fields["support_vampiric_key"].setText("F8")
    settings.apply_field("support_vampiric_key")
    assert settings.read_config().support_vampiric_key == "F8"
    assert settings.read_config().heal_key == "F9"
    assert not editor.toPlainText().startswith("{")
    settings.close()


def test_new_duo_settings_roundtrip(widgets):
    editor, _, _ = widgets
    config = DuoConfig(buff1_key="F5", support_potion_key="F6", support_party_heal_key="F7")
    editor.setPlainText(format_duo_settings(config))
    settings = FarmSettings(editor, duo=True)
    assert settings.read_config() == config
    settings.close()


def test_group_has_room_for_multiple_rows_and_scrolls_one_row(widgets):
    _, settings, app = widgets
    settings.resize(900, 450)
    settings.show()
    app.processEvents()
    scroll = settings.pages.currentWidget()
    assert isinstance(scroll, QScrollArea)
    assert scroll.viewport().height() >= 300
    bar = scroll.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(0)
    wheel = QWheelEvent(QPointF(40, 40), QPointF(40, 40), QPoint(), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(scroll.viewport(), wheel)
    assert 0 < bar.value() <= 30


def test_expanded_settings_resizes_and_edits_same_script(widgets):
    editor, settings, app = widgets
    settings.groups.setCurrentIndex(1)
    settings.expand_settings()
    app.processEvents()
    dialog = settings.findChild(QDialog)
    assert dialog is not None
    expanded = dialog.findChild(FarmSettings)
    assert expanded.groups.currentIndex() == settings.groups.currentIndex()
    assert dialog.width() >= 900
    before = expanded.pages.height()
    dialog.resize(1100, 900)
    app.processEvents()
    assert expanded.pages.height() > before
    expanded.fields["vampiric_key"].setText("F8")
    expanded.apply_field("vampiric_key")
    assert 'set vampiric_key F8' in editor.toPlainText()
    assert settings.fields["vampiric_key"].text() == '"F8"'
    dialog.close()


def test_all_settings_have_visible_russian_descriptions(widgets):
    _, settings, _ = widgets
    for name in DuoConfig.model_fields:
        assert re.search("[А-Яа-я]", setting_description(name))
    for name, field in settings.fields.items():
        label = settings.findChild(QLabel, f"description_{name}")
        assert label is not None
        assert setting_description(name) in label.text()
        assert field.toolTip() == setting_description(name)


def test_boolean_dropdown_changes_script_and_updates_from_text(widgets):
    editor, settings, _ = widgets
    field = settings.fields["regen_enabled"]
    assert isinstance(field, QComboBox)
    assert field.currentData() is False
    assert field.itemText(field.findData(True)) == "Включено"
    field.setCurrentIndex(field.findData(True))
    assert "set regen_enabled true" in editor.toPlainText()
    assert settings.read_config().regen_enabled is True
    field.setCurrentIndex(field.findData(False))
    assert "set regen_enabled false" in editor.toPlainText()
    text = editor.toPlainText().replace("set regen_enabled false", "set regen_enabled true")
    editor.setPlainText(text)
    assert field.currentData() is True
    assert editor.toPlainText() == text


def test_all_boolean_settings_use_dropdowns(widgets):
    _, settings, _ = widgets
    for name, definition in settings.config_type.model_fields.items():
        if definition.annotation is bool:
            assert isinstance(settings.fields[name], QComboBox)
