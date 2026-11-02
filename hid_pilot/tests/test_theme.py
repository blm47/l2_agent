import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QVBoxLayout

from hid_pilot.theme import apply_dark_theme


def _luminance(color: QColor) -> float:
    channels = (color.redF(), color.greenF(), color.blueF())
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
              for value in channels]
    return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))


@pytest.mark.parametrize("foreground,background", [
    (QPalette.ColorRole.WindowText, QPalette.ColorRole.Window),
    (QPalette.ColorRole.Text, QPalette.ColorRole.Base),
    (QPalette.ColorRole.ButtonText, QPalette.ColorRole.Button),
    (QPalette.ColorRole.HighlightedText, QPalette.ColorRole.Highlight),
    (QPalette.ColorRole.PlaceholderText, QPalette.ColorRole.Base),
])
def test_theme_text_has_readable_contrast(foreground, background):
    app = QApplication.instance() or QApplication([])
    apply_dark_theme(app)
    palette = app.palette()
    light, dark = sorted((_luminance(palette.color(foreground)),
                          _luminance(palette.color(background))), reverse=True)
    assert (light + .05) / (dark + .05) >= 4.5


def test_new_dialog_inherits_theme_and_controls_remain_interactive():
    app = QApplication.instance() or QApplication([])
    apply_dark_theme(app)
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    checkbox = QCheckBox("Наблюдение")
    combo = QComboBox()
    combo.addItems(["Выключено", "Включено"])
    layout.addWidget(checkbox)
    layout.addWidget(combo)
    try:
        dialog.show()
        app.processEvents()
        palette = dialog.palette()
        assert palette.color(QPalette.ColorRole.Window).lightness() < 50
        assert palette.color(QPalette.ColorRole.WindowText).lightness() > 200
        checkbox.click()
        combo.setCurrentIndex(1)
        assert checkbox.isChecked()
        assert combo.currentText() == "Включено"
        assert combo.view().palette().color(QPalette.ColorRole.Base).lightness() < 60
    finally:
        dialog.close()
