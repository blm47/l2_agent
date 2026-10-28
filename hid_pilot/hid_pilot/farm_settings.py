"""
Сгруппированные настройки мили-фарма с сохранением текстового сценария.
"""

import json
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QResizeEvent
from PySide6.QtWidgets import (
    QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from hid_pilot.duo import DuoConfig, format_duo_settings, parse_duo_settings
from hid_pilot.farm_melee import MeleeConfig
from hid_pilot.script import script_variables


SETTING_DESCRIPTIONS = {
    "hp_elixir_key": "Кнопка сильного HP-эликсира МА, около +50% HP; по умолчанию NUM2 (N2). Пусто — отключён.",
    "hp_elixir_hp_threshold": "Нажать сильный эликсир при HP МА ниже этой доли; 0.5 = 50%.",
    "hp_elixir_cooldown": "Откат сильного эликсира МА в секундах реального времени; минимум 300 (5 минут).",
    "support_hp_elixir_key": "Кнопка сильного HP-эликсира саппорта, около +50% HP; по умолчанию NUM2 (N2). Пусто — отключён.",
    "support_hp_elixir_hp_threshold": "Нажать сильный эликсир при HP саппорта ниже этой доли; 0.5 = 50%.",
    "support_hp_elixir_cooldown": "Отдельный откат сильного эликсира саппорта в секундах реального времени; минимум 300.",
    "buff_min_hp": "Баффы разрешены при HP МА строго выше этой доли, включая бой; 0.5 = 50%.",
    "buff_min_mp": "Минимальное MP МА перед каждым собственным баффом; 0.1 = 10%.",
    "support_buff_min_mp": "MP саппорта для начала полного баффа; 0.6 = 60%.",
    "support_buff_emergency_hp": "Прерывать баффы при свежем HP МА, саппорта или самона ниже этой доли; 0.35 = 35%. После восстановления повторить бафф.",
    "ud_key": "Кнопка Ultimate Defense МА, удержание 1 секунда; пусто — УД отключён.",
    "ud_cooldown": "Откат УД после нажатия, секунды; по умолчанию 900.",
    "ud_damage_per_second": "Урон HP в секунду выше этой доли; 0.05 = 5%. Нужно 2 наблюдения подряд.",
    "ud_hp_threshold": "Своё HP ниже этой доли; 0.07 = 7%. Нужно 2 наблюдения подряд.",
    "buff_delay_minutes": "Задержка первого баффа МА и саппорта от старта, минуты; 0 — сразу.",
    "aggro_key": "Кнопка агра МА перед первой атакой по монстру; пусто — отключён.",
    "mass_aggro_key": "Масс-агр МА для спасения сопартийцев и самона, точное удержание 300 мс; пусто — отключён.",
    "mass_aggro_cast_seconds": "Время каста масс-агра МА, секунды; по умолчанию 1.",
    "mass_aggro_cooldown": "Откат масс-агра МА от отправки, секунды; минимум 40.",
    "mass_aggro_hp_threshold": "Масс-агр при HP сопартийца строго ниже этой доли; 0.33 = 33%.",
    "mass_aggro_damage_per_second": "Масс-агр при падении HP сопартийца от этой доли максимального HP в секунду; 0.15 = 15%/с.",
    "humanize": "Небольшие случайные вариации обычных нажатий.",
    "random_movement": "Случайные перемещения базового сценария.",
    "next_target": "Кнопка выбора следующей цели.",
    "attack": "Кнопка основной атаки МА.",
    "melee": "Кнопка ближней атаки базового сценария.",
    "loot": "Кнопка подбора лута.",
    "heal": "Кнопка собственного зелья/лечения МА.",
    "rest": "Кнопка сесть для регена.",
    "stand": "Кнопка встать после регена.",
    "pickup_loot": "Включить автолут: подбирать после подтверждённой смерти моба.",
    "loot_before_farm": "Лут перед фармом: дополнительно подбирать после выбора цели, перед атакой. По умолчанию выключено.",
    "target_threshold": "Порог HP цели базового сценария; доля от 0 до 1.",
    "hp_threshold": "Использовать зелье МА при HP ниже этой доли.",
    "mp_threshold": "Начинать реген при MP ниже этой доли.",
    "mp_resume": "Завершать восстановление MP при достижении этой доли.",
    "attack_delay": "Интервал повторов основной атаки, секунды.",
    "heal_delay": "Интервал между зельями МА по длительности их эффекта, секунды. В мили/дуо другие хилы, УД и бой продолжаются.",
    "target_delay": "Ожидание обновления цели после выбора, секунды.",
    "approach_delay": "Время сближения без урона по цели, секунды.",
    "loot_delay": "Старый параметр: в мили/дуо дополнительную паузу не создаёт.",
    "loot_attempts": "Сколько раз нажимать кнопку подбора; по умолчанию 1.",
    "loot_hold": "Длительность каждого нажатия, секунды; по умолчанию 0.05.",
    "target_attempts": "Лимит попыток выбора цели базового сценария.",
    "combat_timeout": "Предельное время одного боя, секунды.",
    "stuck_timeout": "Ожидание прогресса HP цели до признания застревания, секунды.",
    "reposition_delay": "Время перемещения базового сценария, секунды.",
    "reposition_attempts": "Лимит перемещений базового сценария.",
    "progress_epsilon": "Минимальное снижение доли HP цели для подтверждения урона.",
    "rest_timeout": "Максимальное время восстановления HP/MP, секунды.",
    "observation_attempts": "Лимит повторных чтений нераспознанных HP/MP.",
    "observation_delay": "Пауза между повторными чтениями HP/MP, секунды.",
    "observation_movement": "Устаревшая настройка: при ошибках HP/MP движение всегда отключено.",
    "heal_attempts": "Лимит попыток лечения; в дуо у целевых хилов отдельные счётчики.",
    "cycles": "Максимальное количество циклов фарма.",
    "max_hours": "Общий лимит активного времени сценария, часы.",
    "max_seconds": "Общий лимит активного времени сценария, секунды.",
    "regen_enabled": "Садиться на реген при низком HP/MP.",
    "quiet_seconds": "Ожидание без входящего урона перед отдыхом, секунды.",
    "search_moves": "Количество поисковых перемещений WASD за цикл поиска.",
    "search_cycles": "Лимит циклов поиска моба.",
    "hp_rest_threshold": "Начинать реген при HP ниже этой доли.",
    "hp_resume": "Завершать восстановление HP при достижении этой доли.",
    "target_verify_seconds": "Время перепроверки пропавшего/нулевого HP цели, секунды.",
    "target_max_hp_multiplier": "Не атаковать, если максимальное HP цели не меньше максимального HP МА × этот множитель. По умолчанию 10; сравниваются числа OCR, а не проценты полос.",
    "experimental_fast_farm": "без подтверждения смерти. При HP цели ниже fast_target_hp_threshold — next target и атака. Ассист не ждёт снижения HP цели. Overhit только при HP <20%.",
    "fast_target_hp_threshold": "Порог переключения цели в быстром режиме; 0.07 = 7%. Убийство без подтверждения не засчитывается.",
    "damage_keys": "Кнопки дополнительных атак через запятую; пусто — отключены.",
    "overhit_keys": "Кнопки добивания overhit через запятую; пусто — отключены.",
    "damage_interval": "Интервал применения дополнительных атак, секунды.",
    "overhit_interval": "Интервал применения overhit, секунды.",
    "overhit_margin": "Множитель среднего удара для выбора момента добивания.",
    "overhit_hp_threshold": "Доля HP цели для добивания без оценки среднего удара.",
    "overhit_hold_ms": "Удержание кнопки overhit, миллисекунды.",
    "overhit_early_chance": "Вероятность раннего overhit: 0 — никогда, 1 — всегда.",
    "overhit_during_combat": "Применять overhit также в обычном бою по откату.",
    "damage_sample_gap": "Максимальный интервал между замерами урона, секунды.",
    "buff_key": "Первый макрос баффа саппорта во втором окне.",
    "support_buff2_key": "Второй макрос баффа саппорта после первого; пусто — отключён.",
    "support_buff2_seconds": "Ожидание после второго макроса баффа саппорта, секунды.",
    "follow_key": "Макрос выбора МА и следования за ним.",
    "support_key": "Макрос ассиста МА, дебаффа и атаки саппорта.",
    "support_assist_retry_seconds": "Контрольный повтор ассиста по той же живой цели через это число секунд; по умолчанию 5.",
    "support_assist_retries": "Число контрольных повторов ассиста за бой; по умолчанию 1, 0 отключает. Макрос должен допускать повтор.",
    "heal_key": "Макрос выбора и лечения МА; пусто — отключён.",
    "support_self_heal_key": "Макрос выбора себя и лечения саппорта; пусто — отключён.",
    "support_self_heal_threshold": "Собственный хил саппорта при HP ниже этой доли.",
    "support_potion_key": "Кнопка зелья саппорта; пусто — отключена.",
    "support_potion_hp_threshold": "Зелье саппорта при его HP ниже этой доли.",
    "support_potion_cooldown": "Пауза между зельями саппорта, секунды.",
    "support_party_heal_key": "Пати-реген на 15 секунд; удержание 300 мс, начальная попытка и до трёх повторов при низком HP; пусто — отключён.",
    "support_party_heal_threshold": "Пати-хил при HP МА, саппорта или самона ниже этой доли; самон требует разметки панели в окне МА.",
    "support_party_heal_cast_seconds": "Время каста пати-хила, секунды.",
    "support_party_heal_cooldown": "Интервал после расчётного каста перед повтором. До трёх повторов разрешены в течение баффа; затем ждём его окончания. Общая кнопка использует больший откат.",
    "stop_attack_key": "Кнопка отмены макроса баффа саппорта при входящем уроне.",
    "defense_target_key": "Выбор нападающего для защиты во время баффа.",
    "buff_seconds": "Ожидание после первого макроса баффа саппорта, секунды.",
    "rebuff_minutes": "Интервал полного баффа саппорта, минуты.",
    "support_party_slot": "Номер строки саппорта в пати МА: от 1 до 8.",
    "damage_epsilon": "Минимальное падение доли HP/CP для обнаружения входящего урона.",
    "damage_memory_seconds": "Как долго учитывать недавний входящий урон при баффе, секунды.",
    "support_seconds": "Ожидание макроса ассиста/следования во втором окне, секунды.",
    "heal_seconds": "Ожидание целевого хила саппорта, секунды.",
    "heal_threshold": "Лечить МА саппортом при HP ниже этой доли.",
    "heal_cooldown": "Общая пауза между лечебными кастами саппорта, секунды.",
    "poll_seconds": "Интервал наблюдения МА во время баффа, секунды.",
}


def setting_description(name: str) -> str:
    match = re.fullmatch(r"buff([123])_(key|rebuff_minutes|cast_seconds)", name)
    if match:
        slot, suffix = match.groups()
        description = {
            "key": "Кнопка селф/пати-баффа; пусто — отключён.",
            "rebuff_minutes": "Интервал повторного баффа, минуты.",
            "cast_seconds": "Ожидание каста баффа, секунды.",
        }[suffix]
        return f"Бафф {slot}: {description}"
    for prefix, role in (("vampiric_", "МА"), ("support_vampiric_", "саппорта")):
        if name.startswith(prefix):
            return {
                "key": ("Кнопка вампирика саппорта: ассист по любой живой цели МА; при низком HP лечение повторяется по откату. Пусто — отключён."
                        if prefix == "support_vampiric_" else
                        f"Кнопка вампирика {role} по монстру с HP >50%; пусто — отключён."),
                "hp_threshold": f"Вампирик при собственном HP {role} ниже этой доли.",
                "cast_seconds": f"Время каста вампирика {role}, секунды.",
                "cooldown": f"Пауза после каста вампирика {role}, секунды.",
            }[name.removeprefix(prefix)]
    return SETTING_DESCRIPTIONS[name]


def setting_group(name: str) -> str:
    if name.startswith("mass_aggro_"):
        return "МА — масс-агр"
    if name.startswith("hp_elixir_"):
        return "МА — зелье"
    if name.startswith("support_hp_elixir_"):
        return "Саппорт — зелье"
    if name == "target_max_hp_multiplier":
        return "МА — бой"
    if name in {"experimental_fast_farm", "fast_target_hp_threshold"}:
        return "МА — быстрый фарм"
    if name.startswith("ud_"):
        return "МА — Ультимат Дефенс"
    if name.startswith("support_party_heal"):
        return "Саппорт — пати-хил"
    if name.startswith("support_vampiric"):
        return "Саппорт — собственный хил"
    if name.startswith("support_potion"):
        return "Саппорт — зелье"
    if name.startswith("support_self_heal"):
        return "Саппорт — собственный хил"
    if name in {"buff_delay_minutes", "buff_min_hp", "buff_min_mp"} or name.startswith(("buff1_", "buff2_", "buff3_")):
        return "МА — селф и пати-баффы"
    if name.startswith("vampiric_"):
        return "МА — вампирик"
    if name in {"heal", "hp_threshold", "heal_delay"}:
        return "МА — зелье"
    if name.startswith(("buff_", "rebuff_", "follow_", "support_", "heal_", "defense_", "stop_attack_")):
        return "Саппорт — макросы и лечение МА"
    if name.startswith(("regen_", "hp_rest", "hp_resume", "mp_", "rest", "stand", "quiet_")):
        return "МА — реген"
    if "loot" in name:
        return "МА — подбор лута"
    if name.startswith(("aggro_", "damage_", "overhit_", "attack", "combat_", "approach_", "melee")):
        return "МА — бой"
    return "Поиск, наблюдение и лимиты"


def encode_setting(value: object) -> str:
    if isinstance(value, list):
        value = ",".join(str(item) for item in value)
    return json.dumps("" if value is None else value, ensure_ascii=False)


def _color_boolean_setting(field: QComboBox) -> None:
    colors = {False: ("#b71c1c", "#ffebee"), True: ("#1b5e20", "#e8f5e9")}
    for index in range(field.count()):
        foreground, background = colors[field.itemData(index)]
        field.setItemData(index, QColor(foreground), Qt.ItemDataRole.ForegroundRole)
        field.setItemData(index, QColor(background), Qt.ItemDataRole.BackgroundRole)
    foreground, background = colors[field.currentData()]
    # Светлый фон и тёмный текст сохраняют читаемость в светлой и тёмной теме.
    field.setStyleSheet(f"QComboBox {{ color: {foreground}; background-color: {background}; }}")


class _SettingDescriptionLabel(QLabel):
    """
    Не позволяет прокручиваемой форме сжать высоту перенесённого описания.
    """

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        # Старый минимум нельзя учитывать при расчёте: после расширения строка должна сжиматься.
        self.setMinimumHeight(0)
        height = self.heightForWidth(self.width())
        if height >= 0 and self.minimumHeight() != height:
            self.setMinimumHeight(height)


class FarmSettings(QWidget):
    def __init__(self, editor: QPlainTextEdit, duo: bool = False,
                 allow_expand: bool = True) -> None:
        super().__init__()
        # Длинные пояснения не должны раздвигать основное окно и соседние панели.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.editor = editor
        self.duo = duo
        self.fields: dict[str, QLineEdit | QComboBox] = {}
        self.config_type = DuoConfig if duo else MeleeConfig
        layout = QVBoxLayout(self)
        hint = QLabel("Пороги HP: 0.5 = 50%. Пустая новая кнопка отключает действие.\n"
                      "rebuff_minutes — минуты; cast_seconds / cooldown — секунды.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.groups = QComboBox()
        row.addWidget(self.groups, 1)
        if allow_expand:
            self.expand_button = QPushButton("Развернуть настройки…")
            self.expand_button.clicked.connect(self.expand_settings)
            row.addWidget(self.expand_button)
        layout.addLayout(row)
        self.pages = QStackedWidget()
        self.pages.setMinimumHeight(320)
        self.groups.currentIndexChanged.connect(self.pages.setCurrentIndex)
        layout.addWidget(self.pages, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        forms: dict[str, QGridLayout] = {}
        for name, definition in self.config_type.model_fields.items():
            if name in {"max_seconds", "loot_delay"}:
                continue
            group = setting_group(name)
            if group not in forms:
                page = QWidget()
                form = QGridLayout(page)
                form.setColumnStretch(0, 1)
                form.setColumnStretch(1, 1)
                form.setAlignment(Qt.AlignmentFlag.AlignTop)
                forms[group] = form
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                # Один шаг колеса прокручивает примерно одну строку формы.
                scroll.verticalScrollBar().setSingleStep(8)
                scroll.setWidget(page)
                self.pages.addWidget(scroll)
                self.groups.addItem(group)
            if definition.annotation is bool:
                field = QComboBox()
                field.addItem("Выключено", False)
                field.addItem("Включено", True)
                _color_boolean_setting(field)
                field.currentIndexChanged.connect(lambda _, field=field: _color_boolean_setting(field))
                field.currentIndexChanged.connect(lambda _, name=name: self.apply_field(name))
            else:
                field = QLineEdit()
                field.editingFinished.connect(lambda name=name: self.apply_field(name))
            field.setObjectName(name)
            description = setting_description(name)
            field.setToolTip(description)
            title = "Быстрый фарм" if name == "experimental_fast_farm" else name
            label = _SettingDescriptionLabel(f"{title}\n{description}")
            label.setObjectName(f"description_{name}")
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            # Сетка считает высоту переносов по фактической ширине всей колонки.
            form = forms[group]
            row_index = form.rowCount()
            form.addWidget(label, row_index, 0)
            form.addWidget(field, row_index, 1, Qt.AlignmentFlag.AlignTop)
            self.fields[name] = field
        for form in forms.values():
            # Свободное место остаётся под формой, а не растягивает строки настроек.
            form.setRowStretch(form.rowCount(), 1)
        editor.textChanged.connect(self.refresh)
        self.refresh()

    def expand_settings(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Настройки дуо-фарма" if self.duo else "Настройки мили-фарма")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setMinimumSize(700, 520)
        dialog.resize(1000, 760)
        layout = QVBoxLayout(dialog)
        expanded = FarmSettings(self.editor, self.duo, allow_expand=False)
        expanded.groups.setCurrentIndex(self.groups.currentIndex())
        layout.addWidget(expanded)
        # Обе формы редактируют тот же сценарий и обновляются через textChanged.
        dialog.show()

    def read_config(self) -> MeleeConfig:
        text = self.editor.toPlainText()
        if self.duo:
            return parse_duo_settings(text)
        return MeleeConfig.model_validate(script_variables(text))

    def refresh(self) -> None:
        active = self.duo or bool(re.search(r"^\s*farm_melee\s", self.editor.toPlainText(), re.M))
        self.setVisible(active)
        if not active:
            return
        try:
            config = self.read_config()
        except ValueError:
            self.status.setText("Исправьте настройки в тексте сценария: значения формы не обновлены.")
            return
        self.status.clear()
        for name, value in config.model_dump(mode="json").items():
            if name in self.fields:
                field = self.fields[name]
                if isinstance(field, QComboBox):
                    blocked = field.blockSignals(True)
                    try:
                        field.setCurrentIndex(field.findData(value))
                    finally:
                        field.blockSignals(blocked)
                    _color_boolean_setting(field)
                else:
                    field.setText(encode_setting(value))

    def apply_field(self, name: str) -> None:
        text = self.editor.toPlainText()
        try:
            if self.duo and text.lstrip().startswith("{"):
                text = format_duo_settings(self.read_config())
            field = self.fields[name]
            value = (encode_setting(field.currentData()) if isinstance(field, QComboBox)
                     else field.text().strip())
            if not value:
                value = '""'
            line = f"set {name} {value}"
            pattern = rf"^\s*set\s+{re.escape(name)}\s+[^\r\n]*"
            if re.search(pattern, text, re.M):
                updated = re.sub(pattern, lambda _: line, text, flags=re.M)
            else:
                updated = line + "\n" + text
            if name == "max_hours":
                updated = re.sub(r"^\s*set\s+max_seconds\s+[^\r\n]*\n?", "", updated, flags=re.M)
            if name == "loot_before_farm":
                updated = re.sub(r"^\s*set\s+pre_attack_loot\s+[^\r\n]*\n?", "", updated, flags=re.M)
            if self.duo:
                parse_duo_settings(updated)
            else:
                MeleeConfig.model_validate(script_variables(updated))
            self.editor.setPlainText(updated)
        except ValueError as exc:
            self.status.setText(str(exc))
