"""
Общий детерминированный сценарий фармера и бафера/хилера с одним Pico.
"""

from collections.abc import Callable
from collections import deque
from pathlib import Path
import json
import shlex

import numpy as np
from pydantic import Field, field_validator, model_validator

from hid_pilot.bars import BarProfile, bar_fraction
from hid_pilot.party import PartyProfile
from hid_pilot.controller import ActionController
from hid_pilot.desktop import Target
from hid_pilot.script import KEYS, script_variables
from hid_pilot.farm_melee import MeleeConfig, MeleeFarmer, prepare_extension

class DuoConfig(MeleeConfig):
    ud_key: str | None = None
    ud_cooldown: float = Field(default=900, ge=1, le=86400)
    ud_damage_per_second: float = Field(default=.05, gt=0, le=1)
    ud_hp_threshold: float = Field(default=.07, gt=0, lt=1)
    support_buff_min_mp: float = Field(default=.6, gt=0, le=1)
    support_buff_emergency_hp: float = Field(default=.2, gt=0, lt=1)
    rest: str = "="
    heal: str = "F2"
    buff_key: str = "F1"
    follow_key: str = "F2"
    support_key: str = "F3"
    heal_key: str | None = None
    support_self_heal_key: str | None = None
    support_potion_key: str | None = None
    support_potion_hp_threshold: float = Field(default=.5, gt=0, le=1)
    support_potion_cooldown: float = Field(default=2, ge=.1, le=300)
    support_vampiric_key: str | None = None
    support_vampiric_hp_threshold: float = Field(default=.5, gt=0, le=1)
    support_vampiric_cast_seconds: float = Field(default=1, ge=.1, le=30)
    support_vampiric_cooldown: float = Field(default=5, ge=.1, le=300)
    support_party_heal_key: str | None = None
    support_party_heal_threshold: float = Field(default=.6, gt=0, le=1)
    support_party_heal_cast_seconds: float = Field(default=1, ge=.1, le=30)
    support_party_heal_cooldown: float = Field(default=5, ge=.1, le=300)
    stop_attack_key: str = "ESC"
    defense_target_key: str = "3"
    buff_seconds: float = Field(default=15, ge=1, le=60)
    rebuff_minutes: float = Field(default=19, gt=0, le=1440)
    support_party_slot: int = Field(default=1, ge=1, le=8)
    damage_epsilon: float = Field(default=.005, ge=0, le=.1)
    damage_memory_seconds: float = Field(default=2, ge=.2, le=5)
    support_seconds: float = Field(default=.3, ge=.1, le=2)
    heal_seconds: float = Field(default=.5, ge=.1, le=2)
    heal_threshold: float = Field(default=.6, gt=0, lt=1)
    support_self_heal_threshold: float = Field(default=.6, gt=0, lt=1)
    heal_cooldown: float = Field(default=2, ge=.5, le=60)
    heal_attempts: int = Field(default=10, ge=1, le=100)
    poll_seconds: float = Field(default=.2, ge=.1, le=1)
    combat_timeout: float = Field(default=90, ge=1, le=300)

    @model_validator(mode="before")
    @classmethod
    def migrate_duo_settings(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        values = data.copy()
        for old, new in (("next_target_key", "next_target"), ("attack_key", "attack"),
                         ("loot_key", "loot"), ("target_seconds", "target_delay"),
                         ("combat_seconds", "combat_timeout"), ("loot_seconds", "loot_delay")):
            if old in values:
                if new in values and values[new] != values[old]:
                    raise ValueError(f"Параметры {old} и {new} задают разные значения")
                values[new] = values.pop(old)
        return values

    @model_validator(mode="after")
    def rebuff_period(self) -> "DuoConfig":
        if self.rebuff_minutes * 60 <= self.buff_seconds:
            raise ValueError("Интервал ребаффа должен быть больше длительности баффа")
        return self

    @field_validator("ud_key", "heal_key", "support_self_heal_key", "support_potion_key",
                     "support_vampiric_key", "support_party_heal_key")
    @classmethod
    def optional_heal_key(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        key = value.strip()
        if key not in KEYS:
            raise ValueError(f"Клавиша {key} не поддерживается")
        return key

    @field_validator("buff_key", "follow_key", "support_key",
                     "stop_attack_key", "defense_target_key")
    @classmethod
    def allowed_key(cls, value: str) -> str:
        if value not in KEYS:
            raise ValueError(f"Клавиша {value} не поддерживается")
        return value


def parse_duo_settings(text: str) -> DuoConfig:
    if len(text) > 100_000:
        raise ValueError("Сценарий слишком большой")
    if text.lstrip().startswith("{"):
        return DuoConfig.model_validate_json(text)
    for line, raw in enumerate(text.splitlines(), 1):
        lexer = shlex.shlex(raw, posix=True)
        lexer.whitespace_split = True
        lexer.escape = ""
        parts = list(lexer)
        if parts and (parts[0] != "set" or len(parts) != 3):
            raise ValueError(f"Строка {line}: настройки дуо задаются как set имя значение")
    return DuoConfig.model_validate(script_variables(text))


def format_duo_settings(config: DuoConfig) -> str:
    lines = ["# Дуо-фарм: настройки мили-фарма и бафера/хилера.",
             "# START запускается с вкладки «Два окна».",
             '# Хил помощника отключается строкой: set heal_key ""', ""]
    for name, value in config.model_dump(mode="json").items():
        if name == "max_seconds":
            # Один параметр времени исключает конфликт часов и секунд при правке.
            continue
        if name == "buff_key":
            lines.extend(["", "# Бафер/хилер во втором окне."])
        if isinstance(value, list):
            value = ",".join(str(item) for item in value)
        encoded = json.dumps("" if value is None else value, ensure_ascii=False)
        lines.append(f"set {name} {encoded}")
    return "\n".join(lines) + "\n"


def prepare_duo(base: Path, farmer: Target, support: Target) -> BarProfile:
    if farmer.hwnd == support.hwnd:
        raise ValueError("Выберите два разных окна")
    profile = BarProfile.model_validate_json((base / "farmer" / "farm-bars.json").read_text("utf-8"))
    if not {"hp", "mp", "target_hp"} <= profile.bars.keys() | profile.numbers.keys():
        raise ValueError("Настройте HP/MP фармера и HP цели на вкладке первого окна")
    left, top, right, bottom = farmer.rect
    if (profile.width, profile.height) != (right - left, bottom - top):
        raise ValueError("Размер окна фармера изменился; повторите разметку")
    prepare_extension(base / "farmer", ("farm-bars.json",))
    return profile


def load_duo_party(base: Path, farmer: Target) -> PartyProfile:
    path = base / "farmer" / "party.json"
    if not path.exists():
        raise ValueError("Настройте пати в окне фармера для наблюдения HP бафера")
    profile = PartyProfile.model_validate_json(path.read_text("utf-8"))
    left, top, right, bottom = farmer.rect
    if (profile.width, profile.height) != (right - left, bottom - top):
        raise ValueError("Размер профиля пати не совпадает с окном фармера")
    return profile


class Rebuffed(Exception):
    pass


class DuoFarmer(MeleeFarmer):
    config: DuoConfig

    def __init__(self, controller: ActionController, farmer: Target, support: Target,
                 profile: BarProfile, config: DuoConfig, notify: Callable[[str], None],
                 observation: Callable[[dict], None] | None = None,
                 party_profile: PartyProfile | None = None) -> None:
        if farmer.hwnd == support.hwnd:
            raise ValueError("Выберите два разных окна")
        if controller.transport.protocol != "HID_PILOT_V4":
            raise RuntimeError("Дуо мили-фарм требует прошивки HID_PILOT_V4; ввод не начат")
        super().__init__(controller, profile, config, notify)
        self.farmer = farmer
        self.support = support
        self.observation = observation or (lambda payload: None)
        self.last_heal = float("-inf")
        self.support_heal_ready: dict[str, float] = {}
        self.heals = 0
        self.self_heals = 0
        self.support_potion_ready = float("-inf")
        self.support_vampiric_ready = float("-inf")
        self.support_party_heal_ready = float("-inf")
        self.party_profile = party_profile
        self.next_buff = self.clock() + config.buff_delay_minutes * 60
        self.buffing = False
        self.support_buff_until: float | None = None
        self.support_buff_started = 0.
        self.buff_mp_reason = ""
        self.previous_resources: dict[str, float | None] = {}
        self.resource_generation = controller.pause_generation
        self.resources_at = float("-inf")
        self.damage_until = float("-inf")
        self.support_pending = False
        self.following = False
        self.follow_generation = controller.pause_generation
        self.attacking = False
        self.ud_ready_at = float("-inf")
        self.ud_hp_samples: deque[tuple[float, float]] = deque(maxlen=128)
        self.ud_damage_rate: float | None = None
        self.ud_generation = controller.pause_generation

    def track_ud_damage(self, hp: float, now: float) -> None:
        if (self.ud_generation != self.controller.pause_generation
                or self.ud_hp_samples and now - self.ud_hp_samples[-1][0] > 1):
            self.ud_hp_samples.clear()
        self.ud_generation = self.controller.pause_generation
        if self.ud_hp_samples and now <= self.ud_hp_samples[-1][0]:
            self.ud_hp_samples.clear()
        self.ud_hp_samples.append((now, hp))
        cutoff = now - 1
        while len(self.ud_hp_samples) > 2 and self.ud_hp_samples[1][0] <= cutoff:
            self.ud_hp_samples.popleft()
        samples = list(self.ud_hp_samples)
        elapsed = min(1., now - samples[0][0])
        self.ud_damage_rate = None
        if elapsed <= 0:
            return
        damage = 0.
        for (before_at, before_hp), (after_at, after_hp) in zip(samples, samples[1:]):
            # Учитываем только потерю HP за часть интервала внутри последней секунды.
            overlap = after_at - max(before_at, cutoff)
            damage += max(0., before_hp - after_hp) * overlap / (after_at - before_at)
        self.ud_damage_rate = damage / elapsed

    def use_ultimate_defense(self, hp: float) -> None:
        if self.config.ud_key is None or hp <= 0 or self.clock() < self.ud_ready_at:
            return
        heavy_damage = (self.ud_damage_rate is not None
                        and self.ud_damage_rate > self.config.ud_damage_per_second + 1e-9)
        if hp >= self.config.ud_hp_threshold and not heavy_damage:
            return
        rate = f"{self.ud_damage_rate:.1%}/с" if self.ud_damage_rate is not None else "неизвестен"
        self.report(f"УД МА: HP {hp:.1%}, наблюдаемый урон {rate}; {self.config.ud_key}")
        self.controller.key(self.config.ud_key, 100)
        self.ud_ready_at = self.clock() + self.config.ud_cooldown

    def track_damage(self, image: np.ndarray, hp: float) -> None:
        resources: dict[str, float | None] = {"farmer_hp": hp}
        if isinstance(self.profile, BarProfile) and "cp" in self.profile.bars.keys() | self.profile.numbers.keys():
            resources["farmer_cp"] = self.profile.read(image, "cp")
        if self.party_profile is not None:
            box = self.party_profile.box("hp", self.config.support_party_slot - 1)
            resources["support_hp"] = bar_fraction(self.profile.crop(image, box), "hp")
            mp_box = self.party_profile.box("mp", self.config.support_party_slot - 1)
            resources["support_mp"] = bar_fraction(self.profile.crop(image, mp_box), "mp")
        now = self.controller.active_time()
        self.track_ud_damage(hp, now)
        if (self.resource_generation != self.controller.pause_generation
                or now - self.resources_at > 1):
            self.previous_resources.clear()
            self.damage_until = float("-inf")
        for name, value in resources.items():
            if name == "support_mp":
                continue
            previous = self.previous_resources.get(name)
            if value is not None and previous is not None and previous - value > self.config.damage_epsilon:
                self.damage_until = now + self.config.damage_memory_seconds
        self.previous_resources = resources
        self.resource_generation = self.controller.pause_generation
        self.resources_at = now

    def update_support_buff(self) -> None:
        if not self.buffing or self.support_buff_until is None:
            return
        now = self.controller.active_time()
        if now >= self.support_buff_until:
            self.buffing = False
            self.support_buff_until = None
            self.next_buff = self.support_buff_started + self.config.rebuff_minutes * 60
            self.report("Макрос баффера завершён по таймеру")
            return
        hp = self.previous_resources.get("support_hp")
        fresh = (self.resource_generation == self.controller.pause_generation
                 and now - self.resources_at <= 1)
        if fresh and hp is not None and hp < self.config.support_buff_emergency_hp:
            self.visit(self.config.stop_attack_key, .1)
            self.buffing = False
            self.support_buff_until = None
            self.report(f"Баффер HP <{self.config.support_buff_emergency_hp:.0%}: "
                        "бафф прерван для спасения; потребуется повтор")

    def cast_buffs(self) -> None:
        if not self.buffing and self.controller.active_time() >= self.next_buff:
            self.buff()
        else:
            super().cast_buffs()

    def buffs_due(self) -> bool:
        return (super().buffs_due()
                or not self.buffing and self.controller.active_time() >= self.next_buff)

    def buff(self) -> None:
        hp, _, _ = self.observe()
        if self.buffing or hp <= self.config.buff_min_hp:
            return
        support_hp = self.previous_resources.get("support_hp")
        if support_hp is not None and support_hp < self.config.support_buff_emergency_hp:
            return
        mp = self.previous_resources.get("support_mp")
        if mp is None or mp < self.config.support_buff_min_mp:
            reason = "MP баффера неизвестно" if mp is None else f"MP баффера {mp:.0%} < {self.config.support_buff_min_mp:.0%}"
            if self.buff_mp_reason != reason:
                self.report(f"Полный бафф отложен: {reason}")
                self.buff_mp_reason = reason
            super().cast_buffs()
            return
        self.buff_mp_reason = ""
        self.report("Полный бафф: баффер кастует до конца, МА может защищаться")
        self.support_buff_started = self.controller.active_time()
        self.visit(self.config.buff_key, .1)
        self.buff_actions += 1
        self.support_buff_until = self.controller.active_time() + self.config.buff_seconds
        self.buffing = True
        self.following = False
        super().cast_buffs()
        while self.buffing:
            self.controller.wait(self.config.poll_seconds)
            self.observe()
            if self.under_attack or self.attacking:
                return
        if not self.attacking and self.next_buff > self.support_buff_started:
            self.visit(self.config.follow_key, self.config.support_seconds)
            self.following = True
            self.follow_generation = self.controller.pause_generation

    def check(self) -> None:
        self.controller.checkpoint()
        if self.controller.active_time() >= self.deadline:
            raise RuntimeError("Достигнут лимит времени совместного фарма")

    def visit(self, key: str, seconds: float) -> None:
        self.check()
        self.controller.switch_window(self.support)
        # При STOP или потере фокуса не перехватываем окно через finally.
        self.controller.key(key, 80)
        self.controller.wait(seconds)
        self.check()
        self.controller.switch_window(self.farmer)

    def on_observation(self, image: np.ndarray, hp: float | None,
                       mp: float | None, target: float | None) -> None:
        if hp is None or mp is None:
            if hp is None:
                self.ud_hp_samples.clear()
                self.ud_damage_rate = None
            else:
                # Для аварийного УД достаточно известного HP, распознавание MP не требуется.
                self.track_ud_damage(hp, self.controller.active_time())
                self.use_ultimate_defense(hp)
            self.previous_resources.clear()
            self.damage_until = float("-inf")
            return
        self.track_damage(image, hp)
        self.use_ultimate_defense(hp)
        dead = target == 0 or (self.last_target is not None and self.last_target.dead is True)
        self.observation({"hp": hp, "mp": mp, "target_hp": target, "dead": dead,
                          "support_hp": self.previous_resources.get("support_hp"),
                          "support_mp": self.previous_resources.get("support_mp"),
                          "support_buff_active": self.buffing,
                          "buffs_pending": self.controller.active_time() >= self.next_buff
                          or any(getattr(self.config, f"buff{slot}_key") is not None
                                 and self.clock() >= self.buff_ready[slot] for slot in range(1, 4))})

    def observe(self) -> tuple[float, float, float | None]:
        previous = self.last_resources
        generation = self.controller.pause_generation
        resource_generation = self.resource_generation
        hp, mp, target = super().observe()
        self.under_attack = self.under_attack or self.controller.active_time() < self.damage_until
        fresh_damage = (previous is not None and generation == resource_generation == self.controller.pause_generation
                        and (hp < previous[0] or self.own_cp is not None and previous[1] is not None
                             and self.own_cp < previous[1]))
        if ((fresh_damage or self.controller.active_time() < self.damage_until)
                and (not self.casting_ma_buff or hp <= self.config.buff_min_hp)):
            # Не меняем погибшую/пропавшую цель во время подтверждения смерти и лута.
            if not self.alive_target(target) and target != 0 and fresh_damage and not self.attacking:
                self.report("Падает HP/CP фармера без живой цели: срочно выбираем агрессора через next target")
                self.invalidate_target()
                self.press(self.config.next_target)
                self.controller.wait(self.config.target_delay)
                hp, mp, target = super().observe()
                self.attacking = False
            if self.alive_target(target) and not self.attacking:
                if self.reject_high_level_target():
                    target = None
                else:
                    self.report("Урон по МА/хиллеру: агр и ответная атака приоритетнее баффов")
                    self.open_with_aggro()
                    super().combat_key(self.config.attack, 100)
                    self.attacking = True
        self.update_support_buff()
        if self.buffing:
            return hp, mp, target
        if self.support_maintenance(hp, target):
            hp, mp, target = super().observe()
        support_hp = self.previous_resources.get("support_hp")
        fresh_support = (self.resource_generation == self.controller.pause_generation
                         and self.controller.active_time() - self.resources_at <= 1)
        if fresh_support and support_hp is not None:
            if support_hp >= self.config.support_self_heal_threshold:
                self.self_heals = 0
            elif (support_hp > 0 and self.config.support_self_heal_key is not None
                  and self.support_heal_key_ready(self.config.support_self_heal_key)
                  and self.controller.active_time() - self.last_heal >= self.config.heal_cooldown):
                if self.self_heals >= self.config.heal_attempts:
                    raise RuntimeError("HP саппорта не восстановился после лимита попыток собственного хила")
                self.report(f"Собственный хил саппорта: HP {support_hp:.0%}")
                self.visit(self.config.support_self_heal_key, self.config.heal_seconds)
                self.mark_support_heal(self.config.support_self_heal_key)
                self.self_heals += 1
                # После собственного хила заново читаем состояние фармера и его цели.
                hp, mp, target = super().observe()
        if hp >= self.config.heal_threshold:
            self.heals = 0
        elif (self.config.heal_key is not None
              and self.support_heal_key_ready(self.config.heal_key)
              and self.controller.active_time() - self.last_heal >= self.config.heal_cooldown):
            if self.heals >= self.config.heal_attempts:
                raise RuntimeError("HP не восстановился после лимита попыток хила")
            self.report(f"Хил фармера: HP {hp:.0%}")
            self.visit(self.config.heal_key, self.config.heal_seconds)
            self.mark_support_heal(self.config.heal_key)
            self.heals += 1
            # Действия по цели используют только новый кадр после возврата.
            hp, mp, target = super().observe()
        return hp, mp, target

    def support_heal_key_ready(self, key: str) -> bool:
        return self.controller.active_time() >= self.support_heal_ready.get(key, float("-inf"))

    def mark_support_heal(self, key: str) -> None:
        self.last_heal = self.controller.active_time()
        config = self.config
        cooldowns = [cooldown for assigned, cooldown in (
            (config.heal_key, config.heal_cooldown),
            (config.support_self_heal_key, config.heal_cooldown),
            (config.support_party_heal_key, config.support_party_heal_cooldown),
            (config.support_vampiric_key, config.support_vampiric_cooldown),
        ) if assigned == key]
        # Одна физическая кнопка не получает отдельные откаты через разные ветки хила.
        self.support_heal_ready[key] = self.last_heal + max(cooldowns, default=0.)

    def support_maintenance(self, hp: float, target: float | None) -> bool:
        now = self.controller.active_time()
        support_hp = self.previous_resources.get("support_hp")
        if (self.resource_generation != self.controller.pause_generation
                or now - self.resources_at > 1):
            return False
        config = self.config
        if (support_hp is not None and 0 < support_hp < config.support_potion_hp_threshold
                and config.support_potion_key is not None and now >= self.support_potion_ready):
            self.report(f"Зелье саппорта: HP {support_hp:.0%}")
            self.visit(config.support_potion_key, .1)
            self.support_potion_ready = self.controller.active_time() + config.support_potion_cooldown
            return True
        if (config.support_party_heal_key is not None and now >= self.support_party_heal_ready
                and self.support_heal_key_ready(config.support_party_heal_key)
                and (0 < hp < config.support_party_heal_threshold
                     or support_hp is not None and 0 < support_hp < config.support_party_heal_threshold)):
            self.report("Пати-хил саппорта")
            self.visit(config.support_party_heal_key, .1)
            self.controller.wait(config.support_party_heal_cast_seconds)
            self.mark_support_heal(config.support_party_heal_key)
            self.support_party_heal_ready = self.last_heal + config.support_party_heal_cooldown
            return True
        if (not self.buffing and config.support_vampiric_key is not None
                and self.support_heal_key_ready(config.support_vampiric_key)
                and support_hp is not None and 0 < support_hp < config.support_vampiric_hp_threshold
                and self.alive_target(target) and target > .5
                and now >= self.support_vampiric_ready
                and now - self.last_heal >= config.heal_cooldown):
            self.report(f"Вампирик саппорта: своё HP {support_hp:.0%}, HP монстра {target:.0%}")
            self.visit_vampiric()
            self.mark_support_heal(config.support_vampiric_key)
            self.support_vampiric_ready = self.controller.active_time() + config.support_vampiric_cooldown
            return True
        return False

    def visit_vampiric(self) -> None:
        self.check()
        self.controller.switch_window(self.support)
        # Игровой макрос ассиста выбирает монстра фармера перед вампириком.
        self.controller.key(self.config.support_key, 80)
        self.controller.wait(self.config.support_seconds)
        self.controller.key(self.config.support_vampiric_key, 80)
        self.check()
        self.controller.switch_window(self.farmer)
        self.controller.wait(self.config.support_vampiric_cast_seconds)
        self.following = False

    def protect(self) -> tuple[float | None, bool]:
        _, _, target = self.observe()
        dead = target == 0 or (self.last_target is not None and self.last_target.dead is True)
        return target, dead

    def combat_key(self, key: str, duration: int) -> None:
        if key in {self.config.attack, *self.config.damage_keys, *self.config.overhit_keys}:
            self.attacking = True
        super().combat_key(key, duration)
        if (self.support_pending and not self.buffing
                and key in {self.config.attack, *self.config.damage_keys, *self.config.overhit_keys}):
            self.support_pending = False
            # Свежий HP фармера и приоритетный хил проверяются до ассиста.
            _, _, target = self.observe()
            if self.alive_target(target):
                self.report("Фармер начал атаку; ассист помощника")
                self.visit(self.config.support_key, self.config.support_seconds)
                self.following = False

    def on_target_missing(self) -> None:
        if self.buffing:
            return
        if not self.following or self.follow_generation != self.controller.pause_generation:
            self.report("Next target не выбрал живую цель: включаем следование саппорта")
            self.visit(self.config.follow_key, self.config.support_seconds)
            self.following = True
            self.follow_generation = self.controller.pause_generation

    def _fight(self, target: float | None = None) -> None:
        previous = self.support_pending
        self.support_pending = True
        try:
            super()._fight(target)
        finally:
            self.support_pending = previous
            self.attacking = False

    def run(self) -> None:
        self.controller.switch_window(self.farmer)
        self.observe()
        cycle = 0
        while cycle < self.config.cycles:
            try:
                self.run_cycle(cycle)
                cycle += 1
            except Rebuffed:
                self.report("Бафф завершён; заново ищем живую цель")
        self.report("Совместный фарм завершён")
