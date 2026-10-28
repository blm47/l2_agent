import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette, QWheelEvent
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
    assert settings.fields["buff_min_mp"].parentWidget() is settings.fields["buff1_key"].parentWidget()
    assert settings.read_config().buff_min_mp == .1


def test_strong_hp_elixir_settings_share_potion_groups_for_each_role(widgets):
    editor, _, app = widgets
    editor.setPlainText(format_duo_settings(DuoConfig()))
    settings = FarmSettings(editor, duo=True)
    try:
        assert settings.read_config().hp_elixir_key == "NUM2"
        assert settings.read_config().support_hp_elixir_key == "NUM2"
        titles = [settings.groups.itemText(index) for index in range(settings.groups.count())]
        assert "МА — сильный HP-эликсир" not in titles
        assert "Саппорт — сильный HP-эликсир" not in titles
        for suffix in ("key", "hp_threshold", "cooldown"):
            assert settings.fields[f"hp_elixir_{suffix}"].parentWidget() is settings.fields["heal"].parentWidget()
            assert settings.fields[f"support_hp_elixir_{suffix}"].parentWidget() is settings.fields["support_potion_key"].parentWidget()
        assert settings.fields["hp_elixir_key"].parentWidget() is not settings.fields["support_hp_elixir_key"].parentWidget()
        settings.fields["support_hp_elixir_hp_threshold"].setText("0.4")
        settings.apply_field("support_hp_elixir_hp_threshold")
        assert settings.read_config().support_hp_elixir_hp_threshold == .4
        assert settings.read_config().hp_elixir_hp_threshold == .5
        settings.fields["hp_elixir_key"].setText('""')
        settings.apply_field("hp_elixir_key")
        assert settings.read_config().hp_elixir_key is None
        assert settings.read_config().support_hp_elixir_key == "NUM2"
        app.processEvents()
    finally:
        settings.close()


def test_assist_retry_settings_can_be_adjusted_and_disabled(widgets):
    editor, _, app = widgets
    editor.setPlainText(format_duo_settings(DuoConfig()))
    settings = FarmSettings(editor, duo=True)
    try:
        assert settings.read_config().support_assist_retry_seconds == 5
        assert settings.read_config().support_assist_retries == 1
        settings.fields["support_assist_retries"].setText("0")
        settings.apply_field("support_assist_retries")
        assert settings.read_config().support_assist_retries == 0
        app.processEvents()
    finally:
        settings.close()


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


def test_experimental_fast_farm_gui_edits_mode_and_threshold(widgets):
    _, settings, _ = widgets
    toggle = settings.fields["experimental_fast_farm"]
    threshold = settings.fields["fast_target_hp_threshold"]
    assert isinstance(toggle, QComboBox)
    assert toggle.currentData() is False
    assert threshold.parentWidget() is toggle.parentWidget()
    titles = [settings.groups.itemText(index) for index in range(settings.groups.count())]
    assert "МА — быстрый фарм" in titles
    assert not any("эксперимент" in title.lower() for title in titles)
    label = settings.findChild(QLabel, "description_experimental_fast_farm")
    assert label.text().startswith("Быстрый фарм\nбез подтверждения смерти")
    assert settings.read_config().fast_target_hp_threshold == .07
    toggle.setCurrentIndex(toggle.findData(True))
    threshold.setText("0.09")
    settings.apply_field("fast_target_hp_threshold")
    assert settings.read_config().experimental_fast_farm
    assert settings.read_config().fast_target_hp_threshold == .09


def test_hp_multiplier_is_editable_in_combat_settings(widgets):
    _, settings, _ = widgets
    field = settings.fields["target_max_hp_multiplier"]
    assert field.parentWidget() is settings.fields["damage_keys"].parentWidget()
    assert settings.read_config().target_max_hp_multiplier == 10
    field.setText("5")
    settings.apply_field("target_max_hp_multiplier")
    assert settings.read_config().target_max_hp_multiplier == 5


def test_duo_groups_and_legacy_json_editing(widgets):
    editor, _, _ = widgets
    editor.setPlainText(DuoConfig(heal_key="F9").model_dump_json())
    settings = FarmSettings(editor, duo=True)
    titles = [settings.groups.itemText(index) for index in range(settings.groups.count())]
    assert "Саппорт — пати-хил" in titles
    own_heal_page = settings.fields["support_self_heal_key"].parentWidget()
    for name in ("support_vampiric_key", "support_vampiric_hp_threshold",
                 "support_vampiric_cast_seconds", "support_vampiric_cooldown"):
        assert settings.fields[name].parentWidget() is own_heal_page
    assert settings.read_config().support_vampiric_hp_threshold == .6
    settings.fields["support_vampiric_key"].setText("F8")
    settings.apply_field("support_vampiric_key")
    assert settings.read_config().support_vampiric_key == "F8"
    assert settings.read_config().heal_key == "F9"
    assert not editor.toPlainText().startswith("{")
    settings.close()


def test_loot_gui_exposes_count_duration_and_optional_opening_with_legacy_migration(widgets):
    editor, settings, _ = widgets
    editor.setPlainText('set rest "="\nset pre_attack_loot true\nfarm_melee "farm-bars.json"\n')
    field = settings.fields["loot_before_farm"]
    assert isinstance(field, QComboBox)
    assert field.currentData() is True
    assert "loot_delay" not in settings.fields
    for name in ("loot_attempts", "loot_hold"):
        assert settings.fields[name].parentWidget() is field.parentWidget()
    field.setCurrentIndex(field.findData(False))
    assert "pre_attack_loot" not in editor.toPlainText()
    config = settings.read_config()
    assert not config.loot_before_farm
    assert config.loot_attempts == 1
    assert config.loot_hold == .05


def test_new_duo_settings_roundtrip(widgets):
    editor, _, _ = widgets
    config = DuoConfig(buff1_key="F5", support_potion_key="F6", support_party_heal_key="F7")
    editor.setPlainText(format_duo_settings(config))
    settings = FarmSettings(editor, duo=True)
    assert settings.read_config() == config
    settings.close()


def test_second_support_buff_fields_have_descriptions_and_save(widgets):
    editor, _, _ = widgets
    editor.setPlainText(format_duo_settings(DuoConfig()))
    settings = FarmSettings(editor, duo=True)
    for name, value in (("support_buff2_key", "F12"), ("support_buff2_seconds", "10")):
        field = settings.fields[name]
        assert field.toolTip() == setting_description(name)
        label = settings.findChild(QLabel, f"description_{name}")
        assert label is not None
        assert setting_description(name) in label.text()
        assert field.parentWidget() is settings.fields["buff_key"].parentWidget()
        field.setText(value)
        settings.apply_field(name)
    assert settings.read_config().support_buff2_key == "F12"
    assert settings.read_config().support_buff2_seconds == 10
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


@pytest.mark.parametrize("duo", [False, True])
def test_boolean_settings_colors_follow_selection_and_script(widgets, duo):
    editor, original, app = widgets
    if duo:
        editor.setPlainText(format_duo_settings(DuoConfig()))
    settings = FarmSettings(editor, duo=True) if duo else original
    colors = {False: ("#b71c1c", "#ffebee"), True: ("#1b5e20", "#e8f5e9")}
    try:
        for name, definition in settings.config_type.model_fields.items():
            if definition.annotation is not bool:
                continue
            field = settings.fields[name]
            for value in (True, False):
                field.setCurrentIndex(field.findData(value))
                app.processEvents()
                field.ensurePolished()
                foreground, background = colors[value]
                assert settings.read_config().model_dump()[name] is value
                assert field.palette().color(QPalette.ColorRole.Text) == QColor(foreground)
                assert field.palette().color(QPalette.ColorRole.Button) == QColor(background)
                for item_value, (item_foreground, item_background) in colors.items():
                    index = field.findData(item_value)
                    assert field.itemData(index, Qt.ItemDataRole.ForegroundRole) == QColor(item_foreground)
                    assert field.itemData(index, Qt.ItemDataRole.BackgroundRole) == QColor(item_background)
        text = 'set rest "="\nset regen_enabled true\n'
        if not duo:
            text += 'farm_melee "farm-bars.json"\n'
        editor.setPlainText(text)
        app.processEvents()
        settings.fields["regen_enabled"].ensurePolished()
        assert settings.fields["regen_enabled"].currentData() is True
        assert settings.fields["regen_enabled"].palette().color(QPalette.ColorRole.Text) == QColor(colors[True][0])
    finally:
        if duo:
            settings.close()


def test_all_boolean_settings_use_dropdowns(widgets):
    _, settings, _ = widgets
    for name, definition in settings.config_type.model_fields.items():
        if definition.annotation is bool:
            assert isinstance(settings.fields[name], QComboBox)


@pytest.mark.parametrize("duo", [False, True])
def test_descriptions_fit_their_rows_in_all_groups_after_resize(widgets, duo, layout_font):
    editor, original, app = widgets
    if duo:
        editor.setPlainText(format_duo_settings(DuoConfig()))
        settings = FarmSettings(editor, duo=True)
    else:
        settings = original
    settings.setFont(layout_font)
    settings.show()
    for width, height in ((970, 908), (700, 520), (1100, 900)):
        settings.resize(width, height)
        for index in range(settings.groups.count()):
            settings.groups.setCurrentIndex(index)
            for _ in range(4):
                app.processEvents()
            page = settings.pages.currentWidget().widget()
            descriptions = sorted(page.findChildren(QLabel), key=lambda label: label.y())
            assert descriptions
            for label in descriptions:
                assert label.height() >= label.heightForWidth(label.width()), label.objectName()
            for before, after in zip(descriptions, descriptions[1:]):
                assert before.geometry().bottom() < after.geometry().top()
    if duo:
        settings.close()


@pytest.fixture
def layout_font(widgets):
    # Offscreen Qt на Windows может не загрузить системные шрифты автоматически.
    font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    yield QFont("Segoe UI", 10)
    if font_id >= 0:
        QFontDatabase.removeApplicationFont(font_id)
