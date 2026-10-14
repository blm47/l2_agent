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


class MeleeConfig(FarmConfig):
    approach_delay: float = Field(default=5, ge=.2, le=30)
    quiet_seconds: float = Field(default=3, ge=3, le=60)
    loot_hold: float = Field(default=1.5, ge=.1, le=3)
    search_moves: int = Field(default=3, ge=1, le=3)
    hp_rest_threshold: float = Field(default=.30, gt=0, lt=1)
    hp_resume: float = Field(default=.90, gt=0, le=1)
    target_verify_seconds: float = Field(default=2, ge=.5, le=5)
    damage_keys: tuple[str, ...] = Field(default=(), max_length=16)
    overhit_keys: tuple[str, ...] = Field(default=(), max_length=16)
    damage_interval: float = Field(default=3, ge=.3, le=300)
    overhit_interval: float = Field(default=5, ge=.3, le=300)
    overhit_margin: float = Field(default=1.15, ge=1, le=2)
    damage_sample_gap: float = Field(default=1, ge=.3, le=2)

    @field_validator("damage_keys", "overhit_keys", mode="before")
    @classmethod
    def skill_keys(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(value.replace(",", " ").split())
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
        if (set(self.damage_keys) | set(self.overhit_keys)) & {self.next_target, self.heal, self.loot, self.rest}:
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

    def monster_identity(self) -> tuple[str, int | None] | None:
        observation = self.last_target
        if (observation is None or observation.kind != "monster"
                or not observation.name or observation.confidence < .9):
            return None
        return observation.name, observation.level

    def average_damage(self, identity: tuple[str, int | None] | None) -> float | None:
        samples = self.damage_samples.get(identity, ())
        return sum(samples) / len(samples) if samples else None

    def damage_status(self) -> str:
        identity = self.monster_identity()
        average = self.average_damage(identity)
        estimate = f"{average:.1%} HP цели" if average is not None else "неизвестно"
        return f"Оценка обычного удара: {estimate}; обучение: {min(3, self.training_kills)}/3 убийств"

    def use_damage_skill(self, target: float, identity: tuple[str, int | None] | None) -> bool:
        average = self.average_damage(identity)
        # Первые три подтверждённых убийства выполняем без дополнительных скиллов.
        if self.training_kills < 3 or average is None:
            return False
        finishing = bool(self.config.overhit_keys) and target <= average * self.config.overhit_margin
        keys = self.config.overhit_keys if finishing else self.config.damage_keys
        interval = self.config.overhit_interval if finishing else self.config.damage_interval
        for key in keys:
            if self.clock() >= self.skill_ready.get(key, 0):
                self.report(f"{'Добивание overhit' if finishing else 'Дополнительный урон'}: {key}; "
                            f"HP цели {target:.1%}, средний удар {average:.1%}")
                self.press(key)
                self.skill_ready[key] = self.clock() + interval
                return True
        return False

    def observe(self) -> tuple[float, float, float | None]:
        hp, mp, target = super().observe()
        if hp < self.config.hp_rest_threshold:
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
        self.last_observation_at = now
        return hp, mp, target

    def before_target_selection(self) -> None:
        if not self.in_recovery:
            hp, mp, _ = self.observe()
            if self.rest_requested or hp < self.config.hp_rest_threshold or mp < self.config.mp_threshold:
                self.recover()

    def acquire(self) -> float:
        for attempt in range(self.config.search_moves + 1):
            self.before_target_selection()
            self.invalidate_target()
            self.press(self.config.next_target)
            self.controller.wait(self.config.target_delay)
            _, _, target = self.observe()
            if target is not None and target > 0 and (self.last_target is None or self.last_target.kind not in {"player", "npc"}):
                return target
            if attempt < self.config.search_moves:
                self.report(f"Нет цели: поиск WASD {attempt + 1}/{self.config.search_moves}")
                self.controller.key(random.choice("WASD"), random.randint(2000, 3000))
        for name in tuple(self.known_mobs):
            self.before_target_selection()
            self.invalidate_target()
            self.controller.target_by_name(name)
            self.controller.wait(self.config.target_delay)
            _, _, target = self.observe()
            observation = self.last_target
            if (target is not None and target > 0 and observation is not None
                    and observation.kind == "monster" and observation.name == name):
                return target
        raise RuntimeError("Next target и /target не нашли мобов; фарм остановлен")

    def verify_target(self, hp: float, mp: float, target: float | None,
                      identity: tuple[str, int | None] | None) -> tuple[float, float, float | None, bool]:
        """
        Перепроверяет неизвестное/нулевое HP, не переключая цель и не повторяя атаку.
        """
        deadline = self.clock() + self.config.target_verify_seconds
        zeros = 1 if target == 0 else 0
        self.report("HP цели не читается или равно нулю: перепроверяем текущую цель")
        while self.clock() < deadline:
            self.controller.wait(min(.15 if zeros else 1 / 3, max(0, deadline - self.clock())))
            hp, mp, target = self.observe()
            current = self.monster_identity()
            if identity is not None and current is not None and current != identity:
                self.report("Во время проверки изменилась цель; смерть не подтверждена")
                return hp, mp, None, False
            if target is not None and target > 0:
                self.report("Живое HP цели снова распознано: продолжаем бой")
                return hp, mp, target, False
            zeros = zeros + 1 if target == 0 else 0
            if zeros >= 2:
                return hp, mp, 0., True
        self.report("Смерть не подтверждена: " + (
            "получаем урон, требуется выбор агрессора" if self.under_attack else "нет достоверного HP цели"))
        return hp, mp, None, False

    def fight(self, target: float | None = None) -> None:
        target = self.acquire() if target is None else target
        identity = self.monster_identity()
        observation = self.last_target
        if (observation is not None and observation.kind == "monster" and observation.confidence >= .9
                and observation.name and (observation.name in self.known_mobs or len(self.known_mobs) < 256)):
            self.known_mobs[observation.name] = observation.level
            if self.catalogue is None:
                self.mob_statistics.remember(observation)
        average = self.average_damage(identity)
        opening_overhit = (self.training_kills >= 3 and average is not None
                           and self.config.overhit_keys and target <= average * self.config.overhit_margin
                           and self.use_damage_skill(target, identity))
        if not opening_overhit:
            self.press(self.config.attack)
        progress_hp, progress_at = target, self.clock()
        started = progress_at
        damaged = False
        deadline = self.clock() + self.config.combat_timeout
        previous_hp, previous_at = target, self.clock()
        samples: list[float] = []
        measuring = not opening_overhit
        kills_before = sum(self.mob_statistics.counts.values())
        next_read = self.clock() + 1 / 3
        while True:
            self.controller.wait(max(0, next_read - self.clock()))
            next_read = self.clock() + 1 / 3
            hp, mp, target = self.observe()
            confirmed_dead = False
            if target is None or target == 0:
                hp, mp, target, confirmed_dead = self.verify_target(hp, mp, target, identity)
                if target is None:
                    self.target_pending = False
                    return
                if target > 0:
                    if progress_hp - target >= self.config.progress_epsilon and target < progress_hp:
                        damaged = True
                    # Разрыв OCR не должен выглядеть одним большим ударом или застреванием.
                    previous_hp, previous_at = target, self.clock()
                    progress_hp, progress_at = target, self.clock()
            current_identity = self.monster_identity()
            if identity is not None and current_identity != identity:
                if current_identity is not None:
                    self.report("Имя/уровень цели изменились: выбираем цель заново")
                    return
                # Временный пропуск OCR имени не означает исчезновения HP цели.
                samples.clear()
                measuring = False
                identity = None
            elif identity is None and current_identity is not None:
                identity = current_identity
                previous_hp, previous_at = target, self.clock()
            if target == 0:
                self.target_pending = False
                if confirmed_dead:
                    self.report("Смерть цели подтверждена")
                    if sum(self.mob_statistics.counts.values()) == kills_before:
                        name = identity[0] if identity is not None and self.monster_identity() == identity else None
                        self.mob_statistics.record_kill(name)
                    self.training_kills += 1
                    if (samples and identity is not None and self.monster_identity() == identity
                            and (identity in self.damage_samples or len(self.damage_samples) < 256)):
                        self.damage_samples.setdefault(identity, deque(maxlen=128)).extend(samples)
                    self.publish_statistics()
                else:
                    self.report("Смерть не подтверждена вторым кадром: продолжаем поиск")
                    return
                if self.config.pickup_loot:
                    self.controller.key(self.config.loot, round(self.config.loot_hold * 1000))
                return
            if hp < self.config.hp_threshold:
                self.press(self.config.heal)
                self.controller.wait(self.config.heal_delay)
                # Макрос лечения может выбрать себя: прежние HP цели больше не используем.
                return
            delta = previous_hp - target
            if (measuring and identity is not None and delta > self.config.progress_epsilon
                    and 0 < self.clock() - previous_at <= self.config.damage_sample_gap):
                # Нулевой HP не участвует: остаток перед смертью занижает размер удара.
                samples.append(delta)
            previous_hp, previous_at = target, self.clock()
            if progress_hp - target >= self.config.progress_epsilon and target < progress_hp:
                progress_hp, progress_at = target, self.clock()
                damaged = True
            timeout = self.config.stuck_timeout if damaged else self.config.approach_delay
            if self.clock() - (progress_at if damaged else started) >= timeout or self.clock() >= deadline:
                self.report("HP цели не снижается: выбираем другую цель")
                return
            if damaged and self.use_damage_skill(target, identity):
                # После первого скилла нельзя отделить его урон от автоатаки по одному HP.
                measuring = False

    def recover(self) -> None:
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
        while self.clock() < deadline:
            hp, mp, _ = self.observe()
            recovering_hp = recovering_hp or hp < self.config.hp_rest_threshold
            recovering_mp = recovering_mp or mp < self.config.mp_threshold
            # Выделенная живая цель сама по себе не доказывает входящий урон.
            if self.under_attack:
                if self.sitting:
                    # При атаке игра сама поднимает персонажа; повтор rest снова посадит его.
                    self.sitting = False
                self.report("Получаем урон: выбираем следующую цель и добиваем агрессора")
                self.fight()
                continue
            if ((not recovering_hp or hp >= self.config.hp_resume)
                    and (not recovering_mp or mp >= self.config.mp_resume)):
                if self.sitting:
                    self.press(self.config.rest)
                    self.sitting = False
                self.rest_requested = False
                return
            if not self.sitting:
                if self.quiet_since is None or self.clock() - self.quiet_since < self.config.quiet_seconds:
                    self.controller.wait(.3)
                    continue
                self.report(f"Урона нет {self.config.quiet_seconds:g} секунд: отдых; HP {hp:.0%}, MP {mp:.0%}")
                self.press(self.config.rest)
                self.sitting = True
            self.controller.wait(.3)
        raise RuntimeError("Таймаут восстановления; фарм остановлен")

    def run(self) -> None:
        for cycle in range(self.config.cycles):
            hp, mp, _ = self.observe()
            # Начальное наблюдение ещё не доказывает наличие агрессора.
            if self.rest_requested or hp < self.config.hp_rest_threshold or mp < self.config.mp_threshold:
                self.recover()
            self.report(f"Фарм воином: цикл {cycle + 1}")
            self.fight()
