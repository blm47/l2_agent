from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


DARK_STYLESHEET = """
QToolTip {
    color: #e4e9f2;
    background-color: #293344;
    border: 1px solid #46556b;
    padding: 5px;
}
QPushButton {
    background-color: #293344;
    border: 1px solid #3b485c;
    border-radius: 5px;
    padding: 4px 12px;
}
QPushButton:hover { background-color: #34435a; border-color: #6386b8; }
QPushButton:pressed { background-color: #1c283b; }
QPushButton:focus { border-color: #79acfa; }
QPushButton:disabled { color: #737f92; background-color: #202733; border-color: #303a49; }
QPushButton[appearance="primary"] { background-color: #285fa9; border-color: #427bc6; }
QPushButton[appearance="primary"]:hover { background-color: #3273c6; }
QPushButton[appearance="primary"]:pressed { background-color: #214d88; }
QPushButton[appearance="pause"] { color: #f5d397; border-color: #7c6742; }
QPushButton[appearance="danger"] { color: #ffced4; background-color: #602e3a; border-color: #985064; }
QPushButton[appearance="danger"]:hover { background-color: #803c4b; }
QPushButton[appearance="danger"]:pressed { background-color: #4b2630; }
QPushButton[appearance="primary"]:disabled { color: #737f92; background-color: #202733; border-color: #303a49; }
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #151c27;
    border: 1px solid #354155;
    border-radius: 4px;
    padding: 3px 6px;
    selection-background-color: #315f98;
    selection-color: #ffffff;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #79acfa; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    color: #737f92;
    background-color: #202733;
    border-color: #303a49;
}
QComboBox QAbstractItemView {
    background-color: #202938;
    color: #e4e9f2;
    selection-background-color: #315f98;
    selection-color: #ffffff;
    border: 1px solid #46556b;
}
QTabWidget::pane { border: 1px solid #354155; border-radius: 5px; }
QTabBar::tab {
    background-color: #202938;
    color: #aebbd0;
    border: 1px solid #354155;
    padding: 4px 12px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:selected { background-color: #2b3b52; color: #ffffff; border-bottom-color: #79acfa; }
QTabBar::tab:hover:!selected { background-color: #29364a; color: #e4e9f2; }
QGroupBox {
    border: 1px solid #354155;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 8px;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #b9cdea; }
QCheckBox, QRadioButton { spacing: 6px; }
QCheckBox:disabled, QRadioButton:disabled, QLabel:disabled { color: #737f92; }
QMenuBar { background-color: #1b2330; }
QMenuBar::item { padding: 5px 10px; background-color: transparent; }
QMenuBar::item:selected, QMenu::item:selected { background-color: #315276; }
QMenu { background-color: #202938; border: 1px solid #46556b; padding: 4px; }
QMenu::item { padding: 5px 24px; }
QMenu::separator { height: 1px; background-color: #354155; margin: 4px; }
QSplitter::handle { background-color: #293344; }
QSplitter::handle:hover { background-color: #6386b8; }
QScrollArea { border: none; }
QLabel[appearance="status"] {
    background-color: #202d40;
    border: 1px solid #3b5475;
    border-radius: 4px;
    padding: 4px 8px;
    color: #d2e3ff;
}
"""


def apply_dark_theme(app: QApplication) -> None:
    """
    Применяет общую тёмную тему к приложению и его диалогам.
    """
    if app.property("hid_pilot_dark_theme"):
        return
    # Fusion сохраняет читаемые системные индикаторы, стрелки и полосы прокрутки.
    app.setStyle("Fusion")
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#1b2330",
        QPalette.ColorRole.WindowText: "#e4e9f2",
        QPalette.ColorRole.Base: "#151c27",
        QPalette.ColorRole.AlternateBase: "#202938",
        QPalette.ColorRole.Text: "#e4e9f2",
        QPalette.ColorRole.Button: "#293344",
        QPalette.ColorRole.ButtonText: "#e4e9f2",
        QPalette.ColorRole.BrightText: "#ffffff",
        QPalette.ColorRole.Highlight: "#315f98",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.ToolTipBase: "#293344",
        QPalette.ColorRole.ToolTipText: "#e4e9f2",
        QPalette.ColorRole.Link: "#8bbaff",
        QPalette.ColorRole.LinkVisited: "#b3a3f5",
        QPalette.ColorRole.PlaceholderText: "#91a0b6",
        QPalette.ColorRole.Light: "#46556b",
        QPalette.ColorRole.Midlight: "#354155",
        QPalette.ColorRole.Mid: "#293344",
        QPalette.ColorRole.Dark: "#10151e",
        QPalette.ColorRole.Shadow: "#0c1119",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText, QPalette.ColorRole.PlaceholderText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#737f92"))
    app.setPalette(palette)
    app.setStyleSheet(DARK_STYLESHEET)
    app.setProperty("hid_pilot_dark_theme", True)
