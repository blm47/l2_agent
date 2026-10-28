"""
Фарм мили-воином: сближение, добивание агрессоров и безопасный отдых.
"""

import random
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from pydantic import Field, field_validator, model_validator

from hid_pilot.bars import BarProfile
from hid_pilot.controller import ActionController
from hid_pilot.farm import FarmConfig, Farmer
from hid_pilot.farm import prepare_extension as prepare_farm
from hid_pilot.script import KEYS
from hid_pilot.numbers import parse_numbers


class MeleeConfig(FarmConfig):
    hp_elixir_key: str | None = "NUM2"
    hp_elixir_hp_threshold: float = Field(default=.5, gt=0, le=1)
    hp_elixir_cooldown: float = Field(default=300, ge=300, le=86400)
    buff_delay_minutes: float = Field(default=5, ge=0, le=1440)
    buff_min_hp: float = Field(default=.5, gt=0, lt=1)
    buff_min_mp: float = Field(default=.1, ge=0, le=1)
    aggro_key: str | None = "2"
    buff1_key: str | None = None
    buff1_rebuff_minutes: float = Field(default=19, gt=0, le=1440)
    buff1_cast_seconds: float = Field(default=1, ge=.1, le=60)
    buff2_key: str | None = None
    buff2_rebuff_minutes: float = Field(default=19, gt=0, le=1440)
    buff2_cast_seconds: float = Field(default=1, ge=.1, le=60)
    buff3_key: str | None = None
    buff3_rebuff_minutes: float = Field(default=19, gt=0, le=1440)
    buff3_cast_seconds: float = Field(default=1, ge=.1, le=60)
    vampiric_key: str | None = None
    vampiric_hp_threshold: float = Field(default=.5, gt=0, le=1)
    vampiric_cast_seconds: float = Field(default=1, ge=.1, le=30)
    vampiric_cooldown: float = Field(default=5, ge=.1, le=300)
    regen_enabled: bool = False
    loot_before_farm: bool = False
    attack_delay: float = Field(default=1.5, ge=.2, le=30)
    rest_timeout: float = Field(default=180, ge=1, le=600)
    observation_attempts: int = Field(default=30, ge=1, le=30)
    approach_delay: float = Field(default=5, ge=.2, le=30)
    quiet_seconds: float = Field(default=2.5, ge=1, le=10)
    loot_attempts: int = Field(default=1, ge=1, le=20)
    loot_hold: float = Field(default=.05, ge=.001, le=3)
    search_moves: int = Field(default=3, ge=1, le=3)
    search_cycles: int = Field(default=30, ge=1)
    hp_rest_threshold: float = Field(default=.30, gt=0, lt=1)
    hp_resume: float = Field(default=.90, gt=0, le=1)
    target_verify_seconds: float = Field(default=2, ge=.5, le=5)
    target_max_hp_multiplier: float = Field(default=10, gt=0)
    experimental_fast_farm: bool = False
    fast_target_hp_threshold: float = Field(default=.07, gt=0, lt=1)
    damage_keys: tuple[str, ...] = Field(default=(), max_length=16)
    overhit_keys: tuple[str, ...] = Field(default=(), max_length=16)
    damage_interval: float = Field(default=3, ge=.3, le=300)
    overhit_interval: float = Field(default=5, ge=.3, le=300)
    overhit_margin: float = Field(default=1.15, ge=1, le=2)
    overhit_hp_threshold: float = Field(default=.02, gt=0, le=.1)
    overhit_hold_ms: int = Field(default=700, ge=100, le=1000)
    overhit_early_chance: float = Field(default=.5, ge=0, le=1)
    overhit_during_combat: bool = False
    stand: str = "-"
    damage_sample_gap: float = Field(default=1, ge=.3, le=2)

    @model_validator(mode="before")
    @classmethod
    def legacy_loot_settings(cls, values: object) -> object:
        if isinstance(values, dict) and "pre_attack_loot" in values:
            values = dict(values)
            previous = values.pop("pre_attack_loot")
            values.setdefault("loot_before_farm", previous)
        return values

    @field_validator("buff1_key", "buff2_key", "buff3_key", "vampiric_key", "aggro_key")
    @classmethod
    def optional_skill_key(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if value not in KEYS:
            raise ValueError(f"Клавиша {value} не поддерживается")
        return value

    @field_validator("hp_elixir_key")
    @classmethod
    def optional_elixir_key(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        key = value.strip().upper()
        if key == "N2":
            key = "NUM2"
        if key not in KEYS:
            raise ValueError("Клавиша сильного HP-эликсира не поддерживается")
        return key

    @field_validator("damage_keys", "overhit_keys", mode="before")
    @classmethod
    def skill_keys(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(value.replace(",", " ").split())
        return value

    @field_validator("stand")
    @classmethod
    def valid_stand_key(cls, value: str) -> str:
        if value not in KEYS:
            raise ValueError("Недопустимая клавиша подъёма")
        return value

    @field_validator("damage_keys", "overhit_keys")
    @classmethod
    def valid_skill_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(key not in KEYS for key in value) or len(set(value)) != len(value):
            raise ValueError("Список скиллов содержит запрещённую или повторную клавишу")
        return value

    @model_validator(mode="after")
    def separate_skills(self) -> "MeleeConfig":
        if self.hp_resume <= self.hp_rest_threshold:
            raise ValueError("hp_resume должен быть больше hp_rest_threshold")
        if set(self.damage_keys) & set(self.overhit_keys):
            raise ValueError("Overhit-клавиши задаются отдельно от дополнительных атак")
        if self.attack in self.overhit_keys:
            raise ValueError("Overhit-клавиша должна отличаться от обычной атаки")
        if (set(self.damage_keys) | set(self.overhit_keys)) & {self.next_target, self.heal, self.loot, self.rest, self.stand}:
            raise ValueError("Клавиши урона не должны совпадать с выбором цели, хилом, лутом или отдыхом")
        return self


def prepare_extension(base: Path, args: tuple[str, ...]) -> None:
    prepare_farm(base, args)


def run_extension(controller: ActionController, base: Path, args: tuple[str, ...],
                  config: MeleeConfig, notify: Callable[[str], None], deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("Достигнут лимит сценария")
    if controller.transport.protocol != "HID_PILOT_V4":
        raise RuntimeError("Фарм воином требует прошивки HID_PILOT_V4; ввод не начат")
    profile = BarProfile.model_validate_json((base / args[0]).read_text("utf-8"))
    config = config.model_copy(update={"max_seconds": min(config.max_seconds, remaining)})
    farmer = MeleeFarmer(controller, profile, config, notify)
    farmer.setup_observation(base)
    try:
        farmer.run()
    finally:
        farmer.close_observation()


class MeleeFarmer(Farmer):
    config: MeleeConfig

    def __init__(self, controller: ActionController, profile: BarProfile, config: MeleeConfig,
                 notify: Callable[[str], None], clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(controller, profile, config, notify, clock)
        self.last_resources: tuple[float, float | None] | None = None
        self.last_mp: float | None = None
        self.quiet_since: float | None = None
        self.under_attack = False
        self.known_mobs: dict[str, int | None] = {}
        self.last_damage: float | None = None
        self.last_observation_at: float | None = None
        self.training_kills = 0
        self.damage_samples: dict[tuple[str, int | None], deque[float]] = {}
        self.skill_ready: dict[str, float] = {}
        self.rest_requested = False
        self.in_recovery = False
        self.current_damage_samples: deque[float] = deque(maxlen=128)
        self.early_overhit_choice: bool | None = None
        self.unreachable_name: str | None = None
        self.unreachable_selections = 0
        self.escape_target: float | None = None
        self.target_name_pool: list[str] = []
        self.unreachable_until: dict[str, float] = {}
        self.chat_commands: deque[float] = deque(maxlen=16)
        self.chat_pause_generation = controller.pause_generation
        self.last_chat_correction = float("-inf")
        self.chat_status = "ожидание первого кадра"
        self.attacking = False
        self.buff_ready: dict[int, float] = {
            slot: self.clock() + config.buff_delay_minutes * 60 for slot in range(1, 4)}
        self.buff_configuration_logged = False
        self.buff_deferred: dict[int, str] = {}
        self.vampiric_ready = float("-inf")
        self.own_heal_ready = float("-inf")
        self.hp_elixir_ready = float("-inf")
        # Игровой откат продолжает идти при PAUSE, поэтому используем реальное время.
        self.hp_elixir_clock = time.monotonic
        self.buff_actions = 0
        self.casting_ma_buff = False
        self.buff_mana_waiting = False
        self.unsafe_target_rejected = False
        self.kill_watch_count = 0
        self.kill_watch_at = self.clock()
        self.kill_watch_attempts = 0
        self.kill_watch_failed_names: set[str] = set()
        self.fast_target_ready = False
        self.verifying_target = False
        self.fast_progress_at: float | None = None

    def sync_kill_watch(self) -> None:
        """
        Только подтверждённое убийство сбрасывает ожидание прогресса фарма.
        """
        count = sum(self.mob_statistics.counts.values())
        if count != self.kill_watch_count:
            self.kill_watch_count = count
            self.kill_watch_at = self.clock()
            self.kill_watch_attempts = 0
            self.kill_watch_failed_names.clear()

    def escape_if_no_kills(self) -> bool:
        """
        Запускает штатный выход при переборе целей без убийств больше двух минут.
        """
        self.sync_kill_watch()
        progress_at = self.kill_watch_at
        if self.config.experimental_fast_farm and self.fast_progress_at is not None:
            progress_at = max(progress_at, self.fast_progress_at)
        elapsed = self.clock() - progress_at
        if (elapsed <= 120 or self.kill_watch_attempts < 2 or self.sitting
                or self.in_recovery or self.under_attack or self.casting_ma_buff
                or getattr(self, "buffing", False)):
            return False
        progress = "прогресса быстрого фарма" if self.config.experimental_fast_farm else "подтверждённых убийств"
        self.report(f"Зависание: {elapsed:.0f} с без {progress}, "
                    f"попыток боя {self.kill_watch_attempts}; начинаем движение и поиск")
        blocked = self.kill_watch_failed_names.copy()
        self.attacking = False
        self.fast_target_ready = False
        self.press("ESC")
        self.target_pending = False
        self.invalidate_target()
        target = self.escape_from_unreachable(None, blocked_names=blocked)
        # Новая попытка выхода не должна запускаться на каждом следующем цикле.
        self.kill_watch_at = self.clock()
        self.kill_watch_count = sum(self.mob_statistics.counts.values())
        self.kill_watch_attempts = 0
        self.kill_watch_failed_names.clear()
        if self.alive_target(target):
            self.fight(target)
        return True

    def buffs_due(self) -> bool:
        return any(getattr(self.config, f"buff{slot}_key") is not None
                   and self.clock() >= self.buff_ready[slot] for slot in range(1, 4))

    def saving_mana_for_buffs(self) -> bool:
        """
        Резервирует ману до выполнения просроченных собственных баффов МА.
        """
        # Полный бафф саппорта использует другую ману и не включает этот режим.
        if not MeleeFarmer.buffs_due(self):
            self.buff_mana_waiting = False
        elif self.last_mp is None or self.last_mp < self.config.buff_min_mp:
            if not self.buff_mana_waiting:
                self.report("Ребафф МА ожидает ману: используем только обычную атаку")
            self.buff_mana_waiting = True
        return self.buff_mana_waiting

    def service_combat_buffs(self, hp: float) -> bool:
        if not self.buff_health_safe(hp) or not self.buffs_due():
            return False
        before = self.buff_actions
        self.cast_buffs()
        return self.buff_actions != before

    def buff_health_safe(self, hp: float) -> bool:
        return hp > self.config.buff_min_hp

    def cast_buffs(self) -> None:
        if not self.buff_configuration_logged:
            enabled = [f"{slot}: {getattr(self.config, f'buff{slot}_key')}"
                       for slot in range(1, 4) if getattr(self.config, f"buff{slot}_key") is not None]
            if enabled:
                self.report(f"Селф/пати-баффы МА настроены: {', '.join(enabled)}")
            else:
                self.report("Селф/пати-баффы МА отключены: buff1_key, buff2_key и buff3_key пусты")
            self.buff_configuration_logged = True
        for slot in range(1, 4):
            key = getattr(self.config, f"buff{slot}_key")
            if key is None or self.clock() < self.buff_ready.get(slot, float("-inf")):
                continue
            hp, mp, _ = self.observe()
            if not self.buff_health_safe(hp):
                reason = "низкое HP"
                if self.buff_deferred.get(slot) != reason:
                    details = (f"; HP {hp:.1%}, порог баффа {self.config.buff_min_hp:.1%}"
                               if reason == "низкое HP" else "")
                    self.report(f"Бафф МА {slot} ({key}) отложен: {reason}{details}")
                    self.buff_deferred[slot] = reason
                return
            if mp is None or mp < self.config.buff_min_mp:
                self.buff_mana_waiting = True
                reason = "MP не распознано" if mp is None else "недостаточно MP"
                if self.buff_deferred.get(slot) != reason:
                    current = "неизвестно" if mp is None else f"{mp:.1%}"
                    self.report(f"Бафф МА {slot} ({key}) отложен: {reason}; "
                                f"MP {current}, требуется ≥{self.config.buff_min_mp:.0%}")
                    self.buff_deferred[slot] = reason
                return
            self.buff_deferred.pop(slot, None)
            if self.sitting:
                self.press(self.config.stand)
                self.sitting = False
            started = self.clock()
            self.report(f"Бафф {slot}: {key}")
            self.casting_ma_buff = True
            try:
                self.press(key)
                self.buff_actions += 1
                completed = self.wait_for_buff(getattr(self.config, f"buff{slot}_cast_seconds"))
            finally:
                self.casting_ma_buff = False
            if not completed:
                self.report(f"Бафф МА {slot} прерван уроном; повторим после боя")
                return
            self.buff_ready[slot] = started + getattr(self.config, f"buff{slot}_rebuff_minutes") * 60
            # Следующий бафф разрешается только после нового наблюдения.

    def wait_for_buff(self, seconds: float) -> bool:
        until = self.clock() + seconds
        generation = self.controller.pause_generation
        while self.clock() < until:
            self.controller.wait(min(.2, until - self.clock()))
            hp, _, _ = self.observe()
            if generation != self.controller.pause_generation or not self.buff_health_safe(hp):
                return False
        return True

    def open_with_aggro(self) -> None:
        if self.reject_unsafe_target():
            return
        if self.config.aggro_key is not None and not self.attacking and not self.saving_mana_for_buffs():
            self.report(f"Агр МА перед атакой: {self.config.aggro_key}")
            self.combat_key(self.config.aggro_key, 100)

    def use_vampiric(self, hp: float, target: float | None) -> bool:
        if (self.reject_unsafe_target() or self.saving_mana_for_buffs() or self.config.vampiric_key is None or hp >= self.config.vampiric_hp_threshold
                or not self.alive_target(target) or target <= .5
                or self.clock() < self.vampiric_ready):
            return False
        self.report(f"Вампирик: своё HP {hp:.1%}, HP монстра {target:.1%}")
        self.combat_key(self.config.vampiric_key, 100)
        self.wait_for_vampiric(self.config.vampiric_cast_seconds)
        self.vampiric_ready = self.clock() + self.config.vampiric_cooldown
        return True

    def wait_for_vampiric(self, seconds: float) -> None:
        self.controller.wait(seconds)

    def combat_key(self, key: str, duration: int) -> bool:
        offensive = {self.config.attack, self.config.aggro_key, self.config.vampiric_key,
                     *self.config.damage_keys, *self.config.overhit_keys}
        if (key in offensive and not self.casting_ma_buff and self.last_target is not None
                and (self.last_target.dead is True or self.last_target.death_marker
                     or self.last_target.hp == 0)):
            self.report("Цель мёртвая по HP или черепу: атакующая команда пропущена")
            return False
        if key in offensive and not self.casting_ma_buff and self.reject_unsafe_target():
            return False
        if (key in offensive and not self.casting_ma_buff and isinstance(self.profile, BarProfile)
                and (self.last_target is None or self.last_target.hp is None or self.last_target.hp <= 0)):
            self.report(f"Живое HP цели не подтверждено: кнопка {key}, агр, атака и скиллы "
                        f"заблокированы; {self.target_description()}")
            return False
        if key in {self.config.attack, *self.config.damage_keys, *self.config.overhit_keys}:
            self.attacking = True
        sent_at = time.monotonic()
        if key in offensive and not self.casting_ma_buff:
            self.report(f"Атакующая команда {key}, удержание {duration} мс: {self.target_description()}")
        self.controller.key(key, duration)
        if (isinstance(self.profile, BarProfile) and self.profile.system_chat_enabled
                and self.identity_cache is not None and self.identity_cache.chat_ready()):
            self.chat_commands.append(sent_at)
        return True

    def press(self, key: str) -> None:
        if key in {self.config.attack, self.config.heal, *self.config.damage_keys, *self.config.overhit_keys}:
            self.combat_key(key, 100)
        else:
            super().press(key)

    def reconcile_system_chat(self) -> None:
        if (not isinstance(self.profile, BarProfile) or not self.profile.system_chat_enabled
                or self.identity_cache is None):
            return
        if self.chat_pause_generation != self.controller.pause_generation:
            self.chat_pause_generation = self.controller.pause_generation
            self.chat_commands.clear()
            self.identity_cache.reset_chat()
            self.chat_status = "новая база после паузы"
            return
        now = time.monotonic()
        for event in self.identity_cache.take_chat_events():
            if not 0 <= now - event.at <= 3:
                continue
            self.chat_status = f"{event.kind}: {event.text}"
            self.report(f"Системный чат [{event.kind}]: {event.text}")
            recent_command = any(0 <= event.at - sent_at <= 3 for sent_at in self.chat_commands)
            if (event.kind == "sitting_block" and recent_command and not self.sitting
                    and now - self.last_chat_correction >= 5):
                self.controller.checkpoint()
                if self.chat_pause_generation != self.controller.pause_generation:
                    self.chat_commands.clear()
                    self.identity_cache.reset_chat()
                    return
                self.controller.key(self.config.stand, 100)
                self.sitting = False
                self.chat_commands.clear()
                self.last_chat_correction = now
                self.report("Системный чат: движение запрещено сидя, хотя скрипт считает персонажа стоящим; "
                            "нажата кнопка подъёма для синхронизации")

    def reset_unreachable(self) -> None:
        self.unreachable_name = None
        self.unreachable_selections = 0

    def alive_target(self, target: float | None) -> bool:
        return (target is not None and target > 0
                and not (self.last_target is not None
                         and (self.last_target.dead is True or self.last_target.death_marker))
                and not self.unsafe_target_rejected and not self.is_unsafe_target())

    def is_unsafe_target(self) -> bool:
        observation = self.last_target
        return self.target_hp_too_large() or observation is not None and (
            observation.kind in {"player", "npc"} or bool(observation.player_class or observation.clan))

    def target_hp_maxima(self) -> tuple[int, int] | None:
        # Сравниваем максимумы из текущего OCR, а не проценты заполнения полос.
        if self.last_target is None:
            return None
        readings = []
        for name in ("hp", "target_hp"):
            source = self.observation_details.get(name, "")
            reading = parse_numbers(source.removeprefix("OCR "), 1.)
            if reading is None:
                return None
            readings.append(reading)
        return readings[0].maximum, readings[1].maximum

    def target_hp_too_large(self) -> bool:
        maxima = self.target_hp_maxima()
        return (maxima is not None
                and maxima[1] >= self.config.target_max_hp_multiplier * maxima[0])

    def reject_unsafe_target(self) -> bool:
        """
        Сбрасывает игрока/NPC без атак и удерживает запрет до новой живой цели.
        """
        if self.is_unsafe_target():
            maxima = self.target_hp_maxima()
            if (maxima is not None
                    and maxima[1] >= self.config.target_max_hp_multiplier * maxima[0]):
                own_maximum, target_maximum = maxima
                name = self.last_target.name or "неизвестно"
                multiplier = self.config.target_max_hp_multiplier
                reason = (f"Максимальное HP цели >= {multiplier:g} × максимальное HP МА; "
                          f"цель «{name}»; максимальное HP МА {own_maximum}; "
                          f"максимальное HP цели {target_maximum}; "
                          f"порог {own_maximum * multiplier:g}")
            else:
                reason = f"Цель — игрок или NPC; {self.target_description()}"
            self.report(f"{reason}: атака запрещена; ESC и поиск моба")
            self.controller.key("ESC", 80)
            self.target_pending = False
            self.attacking = False
            self.fast_target_ready = False
            self.invalidate_target()
            self.unsafe_target_rejected = True
        return self.unsafe_target_rejected

    def escape_from_unreachable(self, name: str | None, *,
                                blocked_names: set[str] | None = None) -> float | None:
        self.kill_watch_at = self.clock()
        self.kill_watch_attempts = 0
        generation = self.controller.pause_generation
        previous_hp, _, _ = self.observe()
        blocked = {name} if name is not None else set()
        if blocked_names is not None:
            blocked.update(blocked_names)
            self.report("Больше двух минут без убийств: штатный выход из застревания для нескольких целей")
        else:
            self.report(f"Пять безуспешных выборов цели {name}: начинаем выход из застревания")
        for blocked_name in blocked:
            self.unreachable_until[blocked_name] = self.clock() + 60
        self.reset_unreachable()
        for round_index in range(2):
            if round_index:
                if self.config.regen_enabled:
                    self.report("Выход не помог: переключаем сесть/встать и повторяем")
                    self.press(self.config.rest)
                    self.sitting = not self.sitting
                else:
                    self.report("Выход не помог: повторяем без сесть/встать, реген отключён")
            direction = random.choice(("WA", "SD"))
            steps = random.randint(20, 30)
            self.report(f"Выход из застревания: попытка {round_index + 1}/2, {direction}, {steps} нажатий")
            for index in range(steps):
                self.controller.checkpoint()
                if generation != self.controller.pause_generation:
                    self.report("Пауза прервала выход из застревания: требуется новое наблюдение")
                    return None
                if self.clock() + 1.5 + self.config.target_delay + .2 > self.deadline:
                    raise RuntimeError("Лимит времени фарма: выход из застревания остановлен")
                self.controller.key(direction[index % 2], 1500)
                if generation != self.controller.pause_generation:
                    self.report("Пауза прервала удержание: прекращаем выход из застревания")
                    return None
                hp, _, _ = self.observe()
                damage = hp < previous_hp
                previous_hp = hp
                # Проверяем агрессора и новую цель после каждого отдельного удержания.
                self.invalidate_target()
                self.press(self.config.next_target)
                self.controller.wait(self.config.target_delay)
                if generation != self.controller.pause_generation:
                    self.report("Пауза во время next target: прекращаем выход из застревания")
                    return None
                hp, _, target = self.observe()
                damage = damage or hp < previous_hp
                previous_hp = hp
                if damage:
                    self.last_damage = self.clock()
                    self.under_attack = True
                    self.sitting = False
                    self.report("Во время выхода падает собственное HP: прекращаем бег и атакуем next target")
                    return target if self.alive_target(target) else None
                identity = self.monster_identity()
                minimum_steps = 4 if blocked_names is not None else 1
                if (index + 1 >= minimum_steps and self.alive_target(target)
                        and identity is not None and identity[0] not in blocked):
                    self.report(f"Выбрана другая цель {identity[0]}: прекращаем бег и атакуем")
                    return target
        for blocked_name in blocked:
            self.unreachable_until[blocked_name] = self.clock() + 60
        self.report("Две попытки выхода завершены без новой цели: возвращаемся к обычному поиску")
        return None

    def monster_identity(self) -> tuple[str, int | None] | None:
        observation = self.last_target
        if (observation is None or observation.kind != "monster"
                or not observation.name or observation.confidence < .9):
            return None
        return observation.name, observation.level

    @staticmethod
    def different_monsters(first: tuple[str, int | None] | None,
                           second: tuple[str, int | None] | None) -> bool:
        """
        Смена цели требует разных имён или двух разных известных уровней.
        """
        return (first is not None and second is not None
                and (first[0] != second[0]
                     or first[1] is not None and second[1] is not None and first[1] != second[1]))

    def average_damage(self, identity: tuple[str, int | None] | None) -> float | None:
        samples = self.damage_samples.get(identity, ())
        return sum(samples) / len(samples) if samples else None

    def damage_status(self) -> str:
        identity = self.monster_identity()
        average = self.estimated_damage(identity)
        estimate = f"{average:.1%} HP цели" if average is not None else "неизвестно"
        text = f"Оценка обычного удара: {estimate}; обучение: {min(3, self.training_kills)}/3 убийств"
        if isinstance(self.profile, BarProfile) and self.profile.system_chat_enabled:
            text += f"\nСистемный чат OCR: {self.chat_status}"
        return text

    def estimated_damage(self, identity: tuple[str, int | None] | None) -> float | None:
        known = self.average_damage(identity)
        if known is not None:
            return known
        samples = self.current_damage_samples
        return sum(samples) / len(samples) if samples else None

    def use_damage_skill(self, target: float, identity: tuple[str, int | None] | None) -> bool:
        if self.reject_unsafe_target() or self.saving_mana_for_buffs():
            return False
        if target <= 0:
            return False
        if self.config.experimental_fast_farm:
            finishing = target < .2 and bool(self.config.overhit_keys)
            keys = self.config.overhit_keys if finishing else self.config.damage_keys
            interval = self.config.overhit_interval if finishing else self.config.damage_interval
            for key in keys:
                if key == self.config.aggro_key or self.clock() < self.skill_ready.get(key, 0):
                    continue
                self.report(f"Быстрый фарм — {'overhit' if finishing else 'дополнительный урон'}: "
                            f"{key}; HP цели {target:.1%}")
                if not self.combat_key(key, self.config.overhit_hold_ms if finishing else 100):
                    return False
                self.skill_ready[key] = self.clock() + interval
                return True
            return False
        average = self.estimated_damage(identity)
        fallback = bool(self.config.overhit_keys) and 0 < target <= self.config.overhit_hp_threshold
        if self.config.overhit_during_combat:
            for key in self.config.overhit_keys:
                if self.clock() >= self.skill_ready.get(key, 0):
                    finishing = fallback or (self.training_kills >= 3 and average is not None
                                            and target <= average * self.config.overhit_margin)
                    self.report(f"{'Добивание overhit' if finishing else 'Overhit-скилл по откату'}: "
                                f"{key}; HP цели {target:.1%}")
                    self.combat_key(key, self.config.overhit_hold_ms)
                    self.skill_ready[key] = self.clock() + self.config.overhit_interval
                    return True
        # Первые три подтверждённых убийства выполняем без дополнительных скиллов.
        if not fallback and (self.training_kills < 3 or average is None):
            return False
        finishing = fallback or bool(self.config.overhit_keys) and average is not None and target <= average * self.config.overhit_margin
        early_band = (not finishing and bool(self.config.overhit_keys) and average is not None
                      and target <= average * (self.config.overhit_margin + 1))
        if early_band and self.config.overhit_early_chance > 0:
            ready = any(self.clock() >= self.skill_ready.get(key, 0) for key in self.config.overhit_keys)
            if ready and self.early_overhit_choice is None:
                # Одно решение на бой: повторные кадры не превращают 50% в почти 100%.
                self.early_overhit_choice = random.random() < self.config.overhit_early_chance
            if not self.early_overhit_choice:
                return False
            finishing = True
        keys = self.config.overhit_keys if finishing else self.config.damage_keys
        interval = self.config.overhit_interval if finishing else self.config.damage_interval
        for key in keys:
            if key == self.config.aggro_key:
                continue
            if self.clock() >= self.skill_ready.get(key, 0):
                estimate = f"{average:.1%}" if average is not None else "ещё не оценён"
                self.report(f"{'Добивание overhit' if finishing else 'Дополнительный урон'}: {key}; "
                            f"HP цели {target:.1%}, средний удар {estimate}")
                if finishing:
                    self.combat_key(key, self.config.overhit_hold_ms)
                else:
                    self.press(key)
                self.skill_ready[key] = self.clock() + interval
                return True
        return False

    def observe(self) -> tuple[float, float, float | None]:
        hp, mp, target = super().observe()
        if self.use_hp_elixir(hp):
            # Не выдаём ожидаемые +50% за измерение: читаем новый кадр после нажатия.
            hp, mp, target = super().observe()
        if target is not None and target > 0 and not self.is_unsafe_target():
            self.unsafe_target_rejected = False
        if self.reject_unsafe_target():
            target = None
        self.reconcile_system_chat()
        if self.config.regen_enabled and hp < self.config.hp_rest_threshold:
            self.rest_requested = True
        observation = self.last_target
        if (observation is not None and observation.kind == "monster" and observation.name
                and observation.confidence >= .9 and len(self.known_mobs) < 256):
            self.known_mobs[observation.name] = observation.level
        cp = getattr(self, "own_cp", None)
        now = self.clock()
        previous = self.last_resources
        damage = previous is not None and (hp < previous[0] or
                  cp is not None and previous[1] is not None and cp < previous[1])
        if damage or previous is None or self.last_observation_at is None or now - self.last_observation_at > 1:
            self.quiet_since = now
        if isinstance(self.profile, BarProfile) and "cp" in self.profile.bars.keys() | self.profile.numbers.keys() and cp is None:
            self.quiet_since = now
        if damage:
            self.last_damage = now
        self.under_attack = self.last_damage is not None and now - self.last_damage < self.config.quiet_seconds
        self.last_resources = (hp, cp)
        self.last_mp = mp
        self.last_observation_at = now
        self.sync_kill_watch()
        return hp, mp, target

    def use_hp_elixir(self, hp: float | None) -> bool:
        config = self.config
        if (hp is None or not 0 < hp < config.hp_elixir_hp_threshold
                or config.hp_elixir_key is None or self.hp_elixir_clock() < self.hp_elixir_ready):
            return False
        self.report(f"Сильный HP-эликсир МА: HP {hp:.1%}, кнопка {config.hp_elixir_key}; "
                    f"откат {config.hp_elixir_cooldown:g} с")
        self.controller.key(config.hp_elixir_key, 80, exact=True)
        self.hp_elixir_ready = self.hp_elixir_clock() + config.hp_elixir_cooldown
        return True

    def before_target_selection(self) -> None:
        if self.config.regen_enabled and not self.in_recovery:
            hp, mp, _ = self.observe()
            if self.rest_requested or hp < self.config.hp_rest_threshold or mp < self.config.mp_threshold:
                self.recover()

    def acquire(self) -> float:
        if self.fast_target_ready:
            _, _, target = self.observe()
            if self.alive_target(target) and not self.reject_high_level_target():
                return target
            self.fast_target_ready = False
        for cycle in range(self.config.search_cycles):
            self.report(f"Поиск цели: цикл {cycle + 1}/{self.config.search_cycles}")
            target = self.search_cycle()
            if target is not None:
                return target
        raise RuntimeError("Next target и /target не нашли мобов после всех циклов поиска; фарм остановлен")

    def own_level(self) -> int | None:
        return self.display_data[0].get("level") if self.display_data is not None else None

    def reject_high_level_target(self) -> bool:
        observation = self.last_target
        own_level = self.own_level()
        if (own_level is None or observation is None or observation.level is None
                or observation.kind in {"player", "npc"} or observation.level - own_level < 5):
            return False
        if observation.name and observation.confidence >= .9:
            self.known_mobs[observation.name] = observation.level
        self.report(f"Цель выше на 5 уровней или больше: свой уровень {own_level}, "
                    f"уровень цели {observation.level}; ESC и поиск другого имени из пула")
        self.press("ESC")
        self.target_pending = False
        self.fast_target_ready = False
        self.invalidate_target()
        return True

    def search_cycle(self) -> float | None:
        self.unreachable_until = {name: until for name, until in self.unreachable_until.items()
                                  if until > self.clock()}
        for attempt in range(self.config.search_moves + 1):
            self.before_target_selection()
            self.invalidate_target()
            self.press(self.config.next_target)
            self.controller.wait(self.config.target_delay)
            _, _, target = self.observe()
            if self.reject_unsafe_target():
                continue
            if self.reject_high_level_target():
                return self.search_known_target()
            identity = self.monster_identity()
            blocked = identity is not None and identity[0] in self.unreachable_until
            if self.alive_target(target) and (not blocked or self.under_attack):
                return target
            if blocked:
                self.report(f"Пропускаем недоступную цель {identity[0]} после next target")
                self.press("ESC")
                self.invalidate_target()
            self.report("Next target не нашёл подходящую цель: пробуем /target по известным именам")
            target = self.search_known_target()
            if target is not None:
                return target
            if self.under_attack:
                self.report("Получаем урон без живой цели: повторяем next target без поискового WASD")
                self.controller.wait(.2)
                continue
            self.on_target_missing()
            if attempt < self.config.search_moves:
                self.report(f"Нет цели: поиск WASD {attempt + 1}/{self.config.search_moves}")
                self.controller.key(random.choice("WASD"), random.randint(2000, 3000))
        return None

    def on_target_missing(self) -> None:
        """
        Даёт дуо включить следование перед перемещением при неудачном выборе цели.
        """

    def search_known_target(self) -> float | None:
        excluded = set(self.unreachable_until)
        if self.unreachable_selections:
            excluded.add(self.unreachable_name)
        own_level = self.own_level()
        if own_level is not None:
            excluded.update(name for name, level in self.known_mobs.items()
                            if level is not None and level - own_level >= 5)
        eligible = set(self.known_mobs) - excluded
        self.target_name_pool = [name for name in self.target_name_pool if name in eligible]
        if not self.target_name_pool:
            self.target_name_pool = [name for name in self.known_mobs if name in eligible]
            random.shuffle(self.target_name_pool)
        # Сохраняем остаток пула между поисками: успешный /target не возвращает имя в начало.
        while self.target_name_pool:
            name = self.target_name_pool.pop()
            self.before_target_selection()
            self.invalidate_target()
            self.controller.target_by_name(name)
            self.controller.wait(self.config.target_delay)
            _, _, target = self.observe()
            if self.reject_unsafe_target():
                continue
            if self.reject_high_level_target():
                continue
            observation = self.last_target
            if (target is not None and target > 0
                    and (observation is None or observation.kind not in {"player", "npc"})):
                # Живое HP уже подтверждает выделение; задержка OCR ника не требует нового /target.
                self.report("Живая цель выделена через /target: прекращаем перебор и сближаемся")
                return target
        return None

    def verify_target(self, hp: float, mp: float, target: float | None,
                      identity: tuple[str, int | None] | None) -> tuple[float, float, float | None, bool]:
        self.verifying_target = True
        try:
            return self._verify_target(hp, mp, target, identity)
        finally:
            self.verifying_target = False

    def _verify_target(self, hp: float, mp: float, target: float | None,
                       identity: tuple[str, int | None] | None) -> tuple[float, float, float | None, bool]:
        """
        Перепроверяет неизвестное/нулевое HP, не переключая цель и не повторяя атаку.
        """
        deadline = self.clock() + self.config.target_verify_seconds
        zeros = 1 if target == 0 else 0
        markers = 0
        marker_at: float | None = None
        missing = 0
        self.report("HP цели не читается или равно нулю: перепроверяем текущую цель")
        while self.clock() < deadline:
            self.controller.wait(min(.15 if zeros else 1 / 3, max(0, deadline - self.clock())))
            hp, mp, target = self.observe()
            if self.reject_unsafe_target():
                return hp, mp, None, False
            current = self.monster_identity()
            if self.different_monsters(identity, current):
                self.report("Во время проверки изменилась цель; смерть не подтверждена")
                return hp, mp, None, False
            marker = self.last_target is not None and self.last_target.death_marker
            if target is not None and target > 0 and not marker:
                self.report("Живое HP цели снова распознано: продолжаем бой")
                return hp, mp, target, False
            fresh_marker = marker and (marker_at is None or self.last_target.at > marker_at)
            if fresh_marker:
                markers += 1
                marker_at = self.last_target.at
            elif not marker:
                markers = 0
                marker_at = None
            if markers >= 2:
                self.report("Смерть цели подтверждена маркером на двух свежих кадрах")
                return hp, mp, 0., True
            if target == 0 or fresh_marker:
                zeros += 1
                missing = 0
            elif zeros:
                # Один пропуск OCR между нулевыми показаниями допустим.
                missing += 1
                if missing > 1:
                    zeros = 0
                    missing = 0
            if zeros >= 2:
                return hp, mp, 0., True
        self.report("Смерть не подтверждена: " + (
            "получаем урон, требуется выбор агрессора" if self.under_attack else "нет достоверного HP цели"))
        return hp, mp, None, False

    def fight(self, target: float | None = None) -> None:
        try:
            while True:
                self.escape_target = None
                self._fight(target)
                if self.escape_target is None:
                    return
                self.attacking = False
                target = self.escape_target
        finally:
            self.attacking = self.fast_target_ready

    def switch_fast_target(self) -> None:
        """
        Выбирает следующую цель без target_delay и атакует после свежей проверки CV.
        """
        self.fast_target_ready = False
        self.attacking = False
        self.target_pending = False
        self.invalidate_target()
        generation = self.controller.pause_generation
        self.press(self.config.next_target)
        # До первой атаки новой цели не запускаем визиты ассиста во второе окно.
        _, _, target = MeleeFarmer.observe(self)
        if (generation != self.controller.pause_generation
                or not self.alive_target(target) or self.reject_unsafe_target()
                or self.reject_high_level_target()):
            return
        self.fast_target_ready = MeleeFarmer.combat_key(self, self.config.attack, 100)

    def _fight(self, target: float | None = None) -> None:
        target = self.acquire() if target is None else target
        already_attacking = self.fast_target_ready
        self.fast_target_ready = False
        if self.reject_unsafe_target():
            return
        if self.reject_high_level_target():
            self.escape_target = self.search_known_target()
            return
        if (self.config.loot_before_farm and self.config.pickup_loot
                and not self.under_attack and not self.attacking and not already_attacking):
            self.report("Цель выбрана: подбор лута перед атакой")
            self.pickup_loot()
            _, _, target = self.observe()
            if not self.alive_target(target):
                self.report("После подбора лута живая цель не подтверждена; продолжаем поиск")
                return
            if self.reject_high_level_target():
                self.escape_target = self.search_known_target()
                return
        own_hp = f"{self.last_resources[0]:.1%}" if self.last_resources is not None else "?"
        own_mp = f"{self.last_mp:.1%}" if self.last_mp is not None else "?"
        self.report(f"Начало боя: свои HP {own_hp}, MP {own_mp}; HP цели {target:.1%}")
        self.kill_watch_attempts += 1
        self.current_damage_samples.clear()
        self.early_overhit_choice = None
        identity = self.monster_identity()
        # Отдельно помним имя для счётчика недоступности, не для урона или подтверждения смерти.
        last_known_identity = identity
        identity_generation = self.controller.pause_generation
        observation = self.last_target
        if (observation is not None and observation.kind == "monster" and observation.confidence >= .9
                and observation.name and (observation.name in self.known_mobs or len(self.known_mobs) < 256)):
            self.known_mobs[observation.name] = observation.level
            if self.catalogue is None:
                self.mob_statistics.remember(observation)
        average = self.average_damage(identity)
        if not already_attacking:
            self.open_with_aggro()
        opening_overhit = (not already_attacking and self.config.overhit_keys and
                           ((target < .2 if self.config.experimental_fast_farm else
                            self.config.overhit_during_combat or target <= self.config.overhit_hp_threshold or
                            self.training_kills >= 3 and average is not None and
                            target <= average * (self.config.overhit_margin + (1 if self.config.overhit_early_chance > 0 else 0))))
                           and self.use_damage_skill(target, identity))
        next_attack = self.clock() + self.config.attack_delay
        if not opening_overhit and not already_attacking:
            self.press(self.config.attack)
        progress_hp, progress_at = target, self.clock()
        started = progress_at
        damaged = False
        deadline = self.clock() + self.config.combat_timeout
        previous_hp, previous_at = target, self.clock()
        samples: list[float] = []
        measuring = not opening_overhit
        kills_before = sum(self.mob_statistics.counts.values())
        heals = 0
        resume_attack = False
        next_read = self.clock() + 1 / 3
        while True:
            self.controller.wait(max(0, min(next_read, next_attack) - self.clock()))
            next_read = self.clock() + 1 / 3
            hp, mp, target = self.observe()
            if self.reject_unsafe_target():
                return
            if self.reject_high_level_target():
                self.escape_target = self.search_known_target()
                return
            if (self.config.experimental_fast_farm
                    and (target is None or target < self.config.fast_target_hp_threshold
                         or self.last_target is not None and self.last_target.death_marker)):
                if (target is not None and
                        (damaged or progress_hp - target >= self.config.progress_epsilon)):
                    # В этом режиме прогресс — наблюдаемое снижение HP, а не выдуманное убийство.
                    self.fast_progress_at = self.clock()
                if (self.attacking and not self.unsafe_target_rejected
                        and sum(self.mob_statistics.counts.values()) == kills_before):
                    self.mob_statistics.record_unconfirmed_kill()
                    self.publish_statistics()
                reading = "неизвестно" if target is None else f"{target:.1%}"
                self.report(f"Быстрый фарм: HP цели {reading}; next target и атака без проверки добивания")
                self.switch_fast_target()
                return
            confirmed_dead = False
            if (target is None or target == 0
                    or self.last_target is not None and self.last_target.death_marker):
                hp, mp, target, confirmed_dead = self.verify_target(hp, mp, target, identity)
                if target is None:
                    if (self.attacking and not self.unsafe_target_rejected
                            and sum(self.mob_statistics.counts.values()) == kills_before):
                        self.mob_statistics.record_unconfirmed_kill()
                        self.publish_statistics()
                    if self.config.pickup_loot:
                        self.report("Автолут после боя пропущен: смерть цели не подтверждена")
                    self.target_pending = False
                    return
                if target > 0:
                    if progress_hp - target >= self.config.progress_epsilon and target < progress_hp:
                        damaged = True
                        self.reset_unreachable()
                    # Разрыв OCR не должен выглядеть одним большим ударом или застреванием.
                    previous_hp, previous_at = target, self.clock()
                    progress_hp, progress_at = target, self.clock()
            if self.reject_high_level_target():
                self.escape_target = self.search_known_target()
                return
            current_identity = self.monster_identity()
            if identity_generation != self.controller.pause_generation:
                last_known_identity = None
                identity_generation = self.controller.pause_generation
            if self.different_monsters(identity, current_identity):
                if (self.attacking and not self.unsafe_target_rejected
                        and sum(self.mob_statistics.counts.values()) == kills_before):
                    self.mob_statistics.record_unconfirmed_kill()
                    self.publish_statistics()
                self.report("Имя/уровень цели изменились: выбираем цель заново")
                return
            if current_identity is not None:
                last_known_identity = current_identity
            if identity is not None and current_identity != identity:
                if current_identity is None:
                    # Временный пропуск OCR имени не означает исчезновения HP цели.
                    samples.clear()
                    measuring = False
                    identity = None
                elif identity[1] is None and current_identity[1] is not None:
                    identity = current_identity
            elif identity is None and current_identity is not None:
                identity = current_identity
                previous_hp, previous_at = target, self.clock()
            if target == 0:
                self.target_pending = False
                self.reset_unreachable()
                if confirmed_dead:
                    self.report("Смерть цели подтверждена")
                    if sum(self.mob_statistics.counts.values()) == kills_before:
                        name = identity[0] if identity is not None and self.monster_identity() == identity else None
                        self.mob_statistics.record_kill(name)
                    self.training_kills += 1
                    self.sync_kill_watch()
                    if (samples and identity is not None and self.monster_identity() == identity
                            and (identity in self.damage_samples or len(self.damage_samples) < 256)):
                        self.damage_samples.setdefault(identity, deque(maxlen=128)).extend(samples)
                    self.publish_statistics()
                else:
                    self.report("Смерть не подтверждена вторым кадром: продолжаем поиск")
                    return
                if self.config.pickup_loot:
                    self.pickup_loot()
                return
            if self.service_combat_buffs(hp):
                measuring = False
                previous_at = float("-inf")
                progress_at = started = self.clock()
                next_attack = self.clock() + self.config.attack_delay
                resume_attack = True
                continue
            if resume_attack:
                if self.last_target is not None and self.last_target.kind in {"player", "npc"}:
                    self.report("После лечения выбрана другая цель; смерть моба не подтверждена")
                    return
                self.report("После лечения живое HP цели подтверждено: возобновляем атаку")
                next_attack = self.clock() + self.config.attack_delay
                self.press(self.config.attack)
                resume_attack = False
            if self.use_vampiric(hp, target):
                measuring = False
                previous_at = float("-inf")
                progress_at = started = self.clock()
                resume_attack = True
                continue
            if (hp < self.config.hp_threshold and heals < self.config.heal_attempts
                    and self.clock() >= self.own_heal_ready):
                self.report(f"Собственное HP {hp:.1%}, HP цели {target:.1%}: лечение, текущий бой продолжается")
                self.press(self.config.heal)
                heals += 1
                # Откат зелья не должен останавливать CV, аварийный УД и смену цели.
                self.own_heal_ready = self.clock() + self.config.heal_delay
                # После лечения проверяем новый кадр: прежнее HP цели не подтверждает смерть.
                measuring = False
                previous_at = float("-inf")
                progress_at = started = self.clock()
                resume_attack = True
                if heals == self.config.heal_attempts:
                    self.report("Лимит нажатий лечения в текущем бою достигнут; продолжаем добивать цель")
                continue
            delta = previous_hp - target
            if (measuring and delta > 0
                    and 0 < self.clock() - previous_at <= self.config.damage_sample_gap):
                # Нулевой HP не участвует: остаток перед смертью занижает размер удара.
                samples.append(delta)
                self.current_damage_samples.append(delta)
                self.publish_statistics()
            previous_hp, previous_at = target, self.clock()
            if progress_hp - target >= self.config.progress_epsilon and target < progress_hp:
                progress_hp, progress_at = target, self.clock()
                damaged = True
                self.reset_unreachable()
            timeout = self.config.stuck_timeout if damaged else self.config.approach_delay
            if self.under_attack and not damaged:
                # Под входящим уроном не ждём обычные 5–11 секунд сближения
                # с ещё недоступной целью; уже повреждаемую цель проверяем по stuck_timeout.
                timeout = min(timeout, self.config.target_delay + 1)
            if self.clock() - (progress_at if damaged else started) >= timeout or self.clock() >= deadline:
                failed_identity = self.monster_identity() or last_known_identity
                elapsed = self.clock() - (progress_at if damaged else started)
                reason = ("лимит боя" if self.clock() >= deadline else
                          "застревание после урона" if damaged else "сближение без урона")
                label = failed_identity[0] if failed_identity is not None else "неизвестно"
                self.report("HP цели не снижается: сбрасываем недоступную цель через ESC; "
                            f"причина: {reason}; HP {target:.1%}; без прогресса {elapsed:.1f}/{timeout:g} с; "
                            f"последнее подтверждённое имя в этом бою: {label}")
                self.press("ESC")
                self.target_pending = False
                if failed_identity is not None and len(self.kill_watch_failed_names) < 256:
                    self.kill_watch_failed_names.add(failed_identity[0])
                if not damaged and failed_identity is not None:
                    name = failed_identity[0]
                    self.unreachable_selections = self.unreachable_selections + 1 if self.unreachable_name == name else 1
                    self.unreachable_name = name
                    self.report(f"Цель {name} без урона: выбор {self.unreachable_selections}/5")
                elif damaged:
                    self.reset_unreachable()
                else:
                    # Полностью неизвестную цель не приписываем прошлому имени и не стираем прежние неудачи.
                    self.report("Имя недоступной цели не распознано в этом бою: "
                                "счётчик прежних неудачных выборов сохранён без увеличения")
                self.invalidate_target()
                if self.unreachable_selections >= 5:
                    self.escape_target = self.escape_from_unreachable(self.unreachable_name)
                return
            if damaged and self.use_damage_skill(target, identity):
                # После первого скилла нельзя отделить его урон от автоатаки по одному HP.
                measuring = False
                next_attack = self.clock() + self.config.attack_delay
                continue
            if self.clock() >= next_attack:
                if self.last_target is not None and self.last_target.kind in {"player", "npc"}:
                    self.report("Перед повтором атаки распознан игрок или NPC: бой прекращён")
                    return
                next_attack = self.clock() + self.config.attack_delay
                self.press(self.config.attack)

    def pickup_loot(self) -> None:
        duration = round(self.config.loot_hold * 1000)
        for attempt in range(self.config.loot_attempts):
            self.controller.key(self.config.loot, duration, exact=True)
            self.report(f"Автолут: кнопка {self.config.loot} отправлена "
                        f"{attempt + 1}/{self.config.loot_attempts}, удержание {duration} мс")

    def recover(self) -> None:
        if not self.config.regen_enabled:
            self.rest_requested = False
            return
        self.in_recovery = True
        self.quiet_since = self.clock()
        try:
            self._recover()
        finally:
            self.in_recovery = False

    def _recover(self) -> None:
        deadline = self.clock() + self.config.rest_timeout
        recovering_hp = self.rest_requested
        recovering_mp = False
        previous = self.last_resources
        previous_at = self.last_observation_at if self.last_observation_at is not None else self.clock()
        announced = False
        rest_started: float | None = None
        reported_rest_seconds = -1
        while self.clock() < deadline:
            hp, mp, _ = self.observe()
            cp = self.own_cp
            fresh_damage = previous is not None and 0 <= self.clock() - previous_at <= 1 and (hp < previous[0] or
                           cp is not None and previous[1] is not None and cp < previous[1])
            recovering_hp = recovering_hp or hp < self.config.hp_rest_threshold
            recovering_mp = recovering_mp or mp < self.config.mp_threshold
            if not announced:
                self.report(f"Восстановление: HP {hp:.1%}, MP {mp:.1%}; пороги отдыха "
                            f"HP <{self.config.hp_rest_threshold:.0%}, MP <{self.config.mp_threshold:.0%}; "
                            f"ждём {self.config.quiet_seconds:g} с без урона")
                announced = True
            # Старый флаг under_attack после убийства не доказывает нового агрессора.
            if fresh_damage:
                rest_started = None
                reported_rest_seconds = -1
                if self.sitting:
                    # При атаке игра сама поднимает персонажа; повтор rest снова посадит его.
                    self.sitting = False
                previous_cp = "?" if previous[1] is None else f"{previous[1]:.1%}"
                current_cp = "?" if cp is None else f"{cp:.1%}"
                self.last_damage = self.quiet_since = self.clock()
                self.under_attack = True
                self.report(f"Получаем новый урон: HP {previous[0]:.1%} → {hp:.1%}, "
                            f"CP {previous_cp} → {current_cp}, MP {mp:.1%}: "
                            "выбираем следующую цель и добиваем агрессора")
                self.fight()
                # Урон завершённого боя не переносим на первое наблюдение после него.
                previous = None
                continue
            previous = (hp, cp)
            previous_at = self.clock()
            if ((not recovering_hp or hp >= self.config.hp_resume)
                    and (not recovering_mp or mp >= self.config.mp_resume)):
                if self.sitting:
                    self.press(self.config.stand)
                    self.sitting = False
                self.rest_requested = False
                self.report(f"Восстановление завершено: HP {hp:.1%}, MP {mp:.1%}")
                return
            if not self.sitting:
                if self.quiet_since is None or self.clock() - self.quiet_since < self.config.quiet_seconds:
                    self.controller.wait(.3)
                    continue
                self.press(self.config.rest)
                self.sitting = True
                rest_started = self.clock()
            if rest_started is None:
                rest_started = self.clock()
            rest_seconds = int(self.clock() - rest_started)
            if rest_seconds != reported_rest_seconds:
                self.report(f"Урона нет {self.config.quiet_seconds:g} секунд: отдых; HP {hp:.0%}, MP {mp:.0%}; "
                            f"с начала отдыха {rest_seconds} с")
                reported_rest_seconds = rest_seconds
            self.controller.wait(.3)
        raise RuntimeError("Таймаут восстановления; фарм остановлен")

    def run(self) -> None:
        for cycle in range(self.config.cycles):
            self.run_cycle(cycle)

    def run_cycle(self, cycle: int) -> None:
        self.cast_buffs()
        hp, mp, target = self.observe()
        # Начальное наблюдение ещё не доказывает наличие агрессора.
        if self.config.regen_enabled and (
                self.rest_requested or hp < self.config.hp_rest_threshold or mp < self.config.mp_threshold):
            self.recover()
            hp, mp, target = self.observe()
        own_hp = self.last_resources[0] if self.last_resources is not None else hp
        own_mp = self.last_mp if self.last_mp is not None else mp
        self.report(f"Фарм МА: цикл {cycle + 1}; свои HP {own_hp:.1%}, MP {own_mp:.1%}")
        if self.escape_if_no_kills():
            return
        if self.under_attack and self.alive_target(target):
            self.fight(target)
        else:
            self.fight()
