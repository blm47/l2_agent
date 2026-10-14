import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QFont, QImage, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hid_pilot.auto_numbers import NumberCandidate
from hid_pilot.auto_party import PartyCandidate
from hid_pilot.auto_player import PlayerCandidate
from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.party import PartyProfile

LABELS = {"hp": "Моё HP", "mp": "Моё MP", "cp": "Моё CP", "target_hp": "HP цели"}


class ReviewCanvas(QWidget):
    selected = Signal(object)

    def __init__(self, image: np.ndarray) -> None:
        super().__init__()
        height, width = image.shape[:2]
        self.image = QImage(image.data, width, height, image.strides[0],
                            QImage.Format.Format_BGR888).copy()
        self.boxes: dict[str, BarBox] = {}
        self.view_box = BarBox(x1=0, y1=0, x2=1, y2=1)
        self.start: QPointF | None = None
        self.drag_end: QPointF | None = None
        self.editable = True
        self.setMinimumSize(400, 240)

    def viewport(self) -> QRectF:
        width = self.image.width() * (self.view_box.x2 - self.view_box.x1)
        height = self.image.height() * (self.view_box.y2 - self.view_box.y1)
        scale = min(self.width() / width, self.height() / height)
        width, height = width * scale, height * scale
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def normalized(self, point: QPointF) -> QPointF:
        area = self.viewport()
        view = self.view_box
        return QPointF(view.x1 + max(0., min(1., (point.x() - area.left()) / area.width()))
                       * (view.x2 - view.x1),
                       view.y1 + max(0., min(1., (point.y() - area.top()) / area.height()))
                       * (view.y2 - view.y1))

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        area = self.viewport()
        view = self.view_box
        painter.drawImage(area, self.image, QRectF(
            view.x1 * self.image.width(), view.y1 * self.image.height(),
            (view.x2 - view.x1) * self.image.width(),
            (view.y2 - view.y1) * self.image.height()))
        painter.setClipRect(area)

        def mapped(x: float, y: float) -> QPointF:
            return QPointF(area.left() + (x - view.x1) / (view.x2 - view.x1) * area.width(),
                           area.top() + (y - view.y1) / (view.y2 - view.y1) * area.height())

        for name, box in self.boxes.items():
            kind = name.rsplit(": ", 1)[-1]
            color = {"cp": Qt.GlobalColor.yellow, "hp": Qt.GlobalColor.red,
                     "mp": Qt.GlobalColor.cyan, "name": Qt.GlobalColor.green}.get(
                         kind, Qt.GlobalColor.yellow)
            painter.setPen(QPen(color, 1))
            rect = QRectF(mapped(box.x1, box.y1), mapped(box.x2, box.y2))
            painter.drawRect(rect)
            if not name.endswith((": cp", ": hp", ": mp")):
                painter.drawText(rect.topLeft() - QPointF(0, 4),
                                 LABELS.get(name, name.removesuffix(": name")))
        if self.start is not None and self.drag_end is not None:
            painter.setPen(QPen(Qt.GlobalColor.yellow, 2))
            painter.drawRect(QRectF(
                mapped(self.start.x(), self.start.y()),
                mapped(self.drag_end.x(), self.drag_end.y())).normalized())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.editable and event.button() == Qt.MouseButton.LeftButton and self.viewport().contains(event.position()):
            self.start = self.normalized(event.position())
            self.drag_end = self.start

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.start is not None:
            self.drag_end = self.normalized(event.position())
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.start is None or event.button() != Qt.MouseButton.LeftButton:
            return
        end = self.normalized(event.position())
        x1, x2 = sorted((self.start.x(), end.x()))
        y1, y2 = sorted((self.start.y(), end.y()))
        self.start = None
        self.drag_end = None
        self.update()
        if (x2 - x1) * self.image.width() >= 8 and (y2 - y1) * self.image.height() >= 3:
            self.selected.emit(BarBox(x1=x1, y1=y1, x2=x2, y2=y2))


class AutoReview(QDialog):
    def __init__(self, image: np.ndarray, candidates: list[NumberCandidate], parent: QWidget,
                 party_candidates: list[PartyCandidate] | None = None,
                 player_candidates: list[PlayerCandidate] | None = None,
                 initial_profile: BarProfile | None = None) -> None:
        super().__init__(parent)
        self.setFont(QFont("Segoe UI", 10))
        self.setWindowTitle("Автопоиск HP/MP/CP, цели и пати — проверка")
        self.resize(1100, 750)
        self.source_image = image
        self.boxes: dict[str, BarBox] = {}
        self.choices: dict[str, QComboBox] = {}
        self.target_player_panel: BarBox | None = initial_profile.target_player_panel if initial_profile else None
        self.target_config: dict = {}
        self.level_boxes = {key: getattr(initial_profile, key) if initial_profile else None
                            for key in ("own_level", "target_level")}
        if initial_profile is not None:
            self.target_config = {key: value for key, value in initial_profile.model_dump().items()
                                  if key in {"target_panel", "target_hp_presence_box", "target_kind_names",
                                             "target_dead_marker", "target_dead_mask"}}
        layout = QVBoxLayout(self)
        hint = QLabel("Проверьте HP, MP, CP, цель и область пати на снимке.\n"
                      "Для пати проверьте первого участника сверху, полную длину полос и отступы для питомцев.\n"
                      "Кнопка ручной корректировки позволяет выделить панель и исправить разметку всех строк.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        for name, title in LABELS.items():
            choice = QComboBox()
            choice.addItem("Не найдено / не использовать", None)
            for candidate in candidates:
                n = candidate.numbers
                choice.addItem(f"{n.current}/{n.maximum} · OCR {n.confidence:.0%} · {candidate.evidence}",
                               candidate.box)
            suggestions = [i + 1 for i, candidate in enumerate(candidates) if candidate.kind == name]
            if len(suggestions) == 1:
                choice.setCurrentIndex(suggestions[0])
            self.choices[name] = choice
            if not suggestions and initial_profile is not None and name in initial_profile.numbers:
                choice.addItem("Сохранённая ручная область", initial_profile.numbers[name])
                choice.setCurrentIndex(choice.count() - 1)
            choice.currentIndexChanged.connect(self.update_boxes)
            form.addRow(title, choice)
        manual = QPushButton("Вручную указать свои HP / MP / CP и HP цели…")
        manual.clicked.connect(self.edit_own_regions)
        form.addRow(manual)
        levels = QPushButton("Настроить уровень монстра / персонажа…")
        levels.clicked.connect(lambda: self.edit_own_regions("target_level"))
        form.addRow(levels)
        self.party_choice = QComboBox()
        self.party_choice.addItem("Не найдено / не использовать", None)
        for candidate in party_candidates or []:
            self.party_choice.addItem(candidate.evidence, candidate.profile)
        self.party_step = QSpinBox()
        self.party_step.setRange(20, 500)
        self.party_step.setValue(70)
        self.party_choice.currentIndexChanged.connect(self.update_party)
        self.party_step.valueChanged.connect(self.update_boxes)
        form.addRow("Пати", self.party_choice)
        form.addRow("Шаг участников, px", self.party_step)
        self.party_edit = QPushButton("Ручная корректировка пати…")
        self.party_edit.clicked.connect(self.edit_party)
        form.addRow(self.party_edit)
        self.player_choice = QComboBox()
        self.player_choice.addItem("Не найдено / не использовать", None)
        for candidate in player_candidates or []:
            self.player_choice.addItem(candidate.evidence, candidate.box)
        self.player_choice.currentIndexChanged.connect(self.choose_player_panel)
        form.addRow("Общая область цели: имя / класс / клан", self.player_choice)
        self.player_edit = QPushButton("Имя цели над HP: игрок / монстр / NPC…")
        self.player_edit.clicked.connect(self.edit_player_panel)
        form.addRow(self.player_edit)
        target_edit = QPushButton("Панель цели: игрок / NPC / монстр и красный череп…")
        target_edit.clicked.connect(self.edit_target_panel)
        form.addRow(target_edit)
        self.party_error = QLabel()
        form.addRow(self.party_error)
        layout.addLayout(form)
        self.canvas = ReviewCanvas(image)
        self.canvas.editable = False
        layout.addWidget(self.canvas, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                  QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if len(party_candidates or []) == 1:
            self.party_choice.setCurrentIndex(1)
        self.update_boxes()
        if len(player_candidates or []) == 1:
            self.player_choice.setCurrentIndex(1)

    def edit_own_regions(self, initial_mode: str = "hp") -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Ручная разметка статов")
        dialog.resize(1000, 700)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Выберите стат и выделите числа текущее / максимум целиком.\n"
                                "Сохранение применяется после подтверждения основного окна."))
        mode = QComboBox()
        for name, label in LABELS.items():
            mode.addItem(label, name)
        mode.addItem("Мой уровень (цифры слева от статов)", "own_level")
        mode.addItem("Уровень цели (цифры слева от HP)", "target_level")
        mode.addItem("Имя цели над HP (общая область игрока / монстра / NPC)", "target_panel")
        index = mode.findData(initial_mode) if isinstance(initial_mode, str) else -1
        if index >= 0:
            mode.setCurrentIndex(index)
        layout.addWidget(mode)
        canvas = ReviewCanvas(self.source_image)
        selected = self.boxes.copy()
        selected.update({key: value for key, value in self.level_boxes.items() if value is not None})
        panel = self.target_config.get("target_panel")
        if panel is not None:
            selected["target_panel"] = BarBox.model_validate(panel)
        canvas.boxes = selected.copy()
        layout.addWidget(canvas, 1)

        def choose(box: BarBox) -> None:
            selected[mode.currentData()] = box
            canvas.boxes = selected.copy()
            canvas.update()

        canvas.selected.connect(choose)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for name, box in selected.items():
                if name == "target_panel":
                    self.target_player_panel = box
                    self.target_config["target_panel"] = box.model_dump()
                    continue
                if name in self.level_boxes:
                    self.level_boxes[name] = box
                    continue
                choice = self.choices[name]
                choice.addItem("Ручная область", box)
                choice.setCurrentIndex(choice.count() - 1)
            self.update_boxes()

    def choose_player_panel(self) -> None:
        self.target_player_panel = self.player_choice.currentData()
        if self.target_player_panel is not None:
            self.target_config["target_panel"] = self.target_player_panel.model_dump()
        self.update_boxes()

    def update_boxes(self) -> None:
        if not hasattr(self, "canvas"):
            return
        self.boxes = {name: choice.currentData() for name, choice in self.choices.items()
                      if choice.currentData() is not None}
        if "target_hp" in self.boxes:
            self.target_config["target_hp_presence_box"] = self.boxes["target_hp"].model_dump()
        self.canvas.boxes = self.boxes.copy()
        self.canvas.boxes.update({key: value for key, value in self.level_boxes.items() if value is not None})
        if self.target_player_panel is not None:
            self.canvas.boxes["Игрок: ник / класс / клан"] = self.target_player_panel
        for field, label in (("target_panel", "Панель цели"), ("target_hp_presence_box", "Полоса HP цели"),
                             ("target_dead_marker", "Красный череп")):
            if self.target_config.get(field) is not None:
                self.canvas.boxes[label] = BarBox.model_validate(self.target_config[field])
        try:
            party = self.party_profile()
            self.party_error.clear()
            if party:
                regions = [party.box(kind, slot) for kind in ("name", "cp", "hp", "mp")
                           for slot in range(8)]
                self.canvas.boxes["Пати (до 8 участников)"] = BarBox(
                    x1=min(b.x1 for b in regions), x2=max(b.x2 for b in regions),
                    y1=min(b.y1 for b in regions), y2=max(b.y2 for b in regions))
                for slot in range(8):
                    for kind in ("name", "cp", "hp", "mp"):
                        self.canvas.boxes[f"Пати {slot + 1}: {kind}"] = party.box(kind, slot)
        except ValueError as exc:
            self.party_error.setText(str(exc))
        self.canvas.update()

    def edit_player_panel(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("OCR общей области имени цели")
        dialog.resize(1000, 650)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Выделите общую область над HP: имя монстра/NPC или ник, класс и клан игрока. "
                                "Не включайте эмблему, значки и кнопку закрытия."))
        canvas = ReviewCanvas(self.source_image)
        layout.addWidget(canvas, 1)
        selected = [self.target_player_panel]

        def choose(box: BarBox) -> None:
            selected[0] = box
            canvas.boxes = {"Игрок": box}
            canvas.update()

        canvas.selected.connect(choose)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.target_player_panel = selected[0]
            if selected[0] is not None:
                self.target_config["target_panel"] = selected[0].model_dump()
            self.update_boxes()

    def configure_target(self, panel: BarBox, npc_names: list[str],
                         marker: BarBox | None = None, hp_box: BarBox | None = None) -> None:
        height, width = self.source_image.shape[:2]
        known = {name: kind for name, kind in self.target_config.get("target_kind_names", {}).items()
                 if kind == "monster"}
        known.update({name.strip(): "npc" for name in npc_names if name.strip()})
        profile = BarProfile(width=width, height=height, target_panel=panel,
                             target_hp_presence_box=hp_box,
                             target_kind_names=known)
        if marker is not None:
            profile.calibrate_dead_marker(self.source_image, marker)
        # Общая область включает HP; старую область ника/класса можно сохранить отдельно.
        self.target_config = {key: value for key, value in profile.model_dump().items()
                              if key in {"target_panel", "target_hp_presence_box", "target_kind_names",
                                         "target_dead_marker", "target_dead_mask"}}
        self.update_boxes()

    def edit_target_panel(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("OCR панели цели и маркер смерти")
        dialog.resize(1000, 700)
        layout = QVBoxLayout(dialog)
        hint = QLabel("Выделите панель с именем, строками класса/клана и числами HP.\n"
                      "У монстра внизу есть HP, у NPC этой полосы нет. Выделите её место отдельным режимом "
                      "по снимку монстра, чтобы пропуск цифр OCR не путался с NPC.\n"
                      "Красный череп выделяется отдельно по снимку мёртвой цели, без наружного кольца.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(QLabel("Дополнительно известные имена NPC (необязательно, по одному в строке):"))
        names = QPlainTextEdit("\n".join(name for name, value in self.target_config.get("target_kind_names", {}).items()
                                       if value == "npc"))
        names.setMaximumHeight(70)
        layout.addWidget(names)
        mode = QComboBox()
        mode.addItems(["Выделяю панель цели", "Выделяю красный череп", "Выделяю место полосы HP"])
        layout.addWidget(mode)
        canvas = ReviewCanvas(self.source_image)
        layout.addWidget(canvas, 1)
        selected = {key: BarBox.model_validate(self.target_config[key]) if self.target_config.get(key) else None
                    for key in ("target_panel", "target_dead_marker", "target_hp_presence_box")}

        def choose(box: BarBox) -> None:
            selected[("target_panel", "target_dead_marker", "target_hp_presence_box")[mode.currentIndex()]] = box
            canvas.boxes = {key: value for key, value in selected.items() if value is not None}
            canvas.update()

        canvas.selected.connect(choose)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)

        def save() -> None:
            if selected["target_panel"] is None:
                hint.setText("Сначала выделите панель цели на снимке")
                return
            try:
                self.configure_target(selected["target_panel"], names.toPlainText().splitlines(), selected["target_dead_marker"],
                                      selected["target_hp_presence_box"])
            except ValueError as exc:
                hint.setText(str(exc))
                return
            dialog.accept()

        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def update_party(self) -> None:
        party = self.party_choice.currentData()
        if party:
            self.party_step.setValue(party.row_step)
        self.party_step.setEnabled(party is not None)
        self.update_boxes()

    def party_profile(self) -> PartyProfile | None:
        party = self.party_choice.currentData()
        if party is None:
            return None
        offsets = party.row_offsets
        if offsets:
            offsets = [offset + slot * (self.party_step.value() - party.row_step)
                       for slot, offset in enumerate(offsets)]
        return PartyProfile.model_validate(party.model_dump() | {
            "row_step": self.party_step.value(), "row_offsets": offsets})

    def edit_party(self) -> None:
        from hid_pilot.party_review import PartyReview

        try:
            profile = self.party_profile()
        except ValueError:
            profile = self.party_choice.currentData()
        dialog = PartyReview(self.source_image, profile, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.party_choice.addItem("Ручная разметка пати", dialog.profile())
            self.party_choice.setCurrentIndex(self.party_choice.count() - 1)

    def save(self) -> None:
        try:
            self.party_profile()
        except ValueError as exc:
            self.party_error.setText(str(exc))
            return
        self.accept()
