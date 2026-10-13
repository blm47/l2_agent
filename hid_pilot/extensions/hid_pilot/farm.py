"""
Детерминированный цикл фарма с приоритетом собственного HP и MP.
"""

import logging
import random
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hid_pilot.bars import BarProfile
from hid_pilot.controller import ActionController
from hid_pilot.script import KEYS

logger = logging.getLogger("hid_pilot.farm")


def prepare_extension(base: Path, args: tuple[str, ...]) -> None:
    profile = BarProfile.model_validate_json((base / args[0]).read_text("utf-8"))
    if not {"hp", "mp", "target_hp"} <= profile.bars.keys() | profile.numbers.keys():
        raise ValueError("Сначала настройте Моё HP, Моё MP и HP цели")


def run_extension(controller: ActionController, base: Path, args: tuple[str, ...],
                  config: "FarmConfig", notify: Callable[[str], None], deadline: float) -> None:
    profile = BarProfile.model_validate_json((base / args[0]).read_text("utf-8"))
    remaining = max(0., deadline - time.monotonic())
    if remaining <= 0:
        raise RuntimeError("Достигнут лимит сценария")
    config = config.model_copy(update={"max_seconds": min(config.max_seconds, remaining)})
    Farmer(controller, profile, config, notify).run()


class ReacquireTarget(Exception):
    pass


class FarmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    humanize: bool = True
    next_target: str = "3"
    attack: str = "R"
    melee: str = "4"
    loot: str = "5"
    heal: str = "2"
    rest: str
    pickup_loot: bool = True
    target_threshold: float = Field(default=.10, gt=0, lt=1)
    hp_threshold: float = Field(default=.50, gt=0, lt=1)
    mp_threshold: float = Field(default=.10, gt=0, lt=1)
    mp_resume: float = Field(default=.90, gt=0, le=1)
    attack_delay: float = Field(default=1.5, ge=.2, le=30)
    heal_delay: float = Field(default=2, ge=.2, le=30)
    target_delay: float = Field(default=.7, ge=.2, le=10)
    approach_delay: float = Field(default=4, ge=.2, le=30)
    loot_delay: float = Field(default=.7, ge=.2, le=10)
    loot_attempts: int = Field(default=3, ge=1, le=20)
    target_attempts: int = Field(default=10, ge=1, le=100)
    combat_timeout: float = Field(default=60, ge=1, le=300)
    stuck_timeout: float = Field(default=5, ge=.5, le=60)
    reposition_delay: float = Field(default=3, ge=.2, le=30)
    reposition_attempts: int = Field(default=3, ge=1, le=20)
    progress_epsilon: float = Field(default=.002, ge=0, le=.1)
    rest_timeout: float = Field(default=120, ge=1, le=600)
    observation_attempts: int = Field(default=5, ge=1, le=20)
    observation_delay: float = Field(default=.5, ge=.2, le=5)
    observation_movement: bool = True
    heal_attempts: int = Field(default=5, ge=1, le=20)
    cycles: int = Field(default=1000, ge=1, le=1000)
    max_seconds: float = Field(default=36000, ge=1, le=36000)

    @field_validator("next_target", "attack", "melee", "loot", "heal", "rest")
    @classmethod
    def allowed(cls, value: str) -> str:
        if value not in KEYS:
            raise ValueError(f"Клавиша {value} не поддерживается")
        return value

    @model_validator(mode="after")
    def thresholds(self) -> "FarmConfig":
        if self.mp_resume <= self.mp_threshold:
            raise ValueError("mp_resume должен быть больше mp_threshold")
        return self


class Farmer:
    def __init__(self, controller: ActionController, profile: BarProfile, config: FarmConfig,
                 notify: Callable[[str], None], clock: Callable[[], float] = time.monotonic) -> None:
        self.controller = controller
        self.controller.humanize = config.humanize
        if controller.transport.protocol == "L2_PICO_V1" and "=" in (
            config.next_target, config.attack, config.melee, config.loot, config.heal, config.rest
        ):
            raise RuntimeError("Фарм с клавишей = требует прошивки HID_PILOT_V1; ввод не начат")
        self.profile = profile
        if isinstance(profile.numbers, dict) and profile.numbers:
            from hid_pilot.numbers import shared_numbers
            shared_numbers().prepare()
        self.config = config
        self.notify = notify
        self.clock = controller.active_time if clock is time.monotonic else clock
        self.deadline = self.clock() + config.max_seconds
        self.sitting = False
        self.target_pending = False
        self.observation_details: dict[str, str] = {}

    def report(self, text: str) -> None:
        logger.info(text)
        self.notify(text)

    def observe(self) -> tuple[float, float, float | None]:
        for attempt in range(self.config.observation_attempts):
            if isinstance(self.profile, BarProfile) and self.profile.numbers:
                from hid_pilot.numbers import shared_numbers
                # Дожидаемся фонового OCR до capture, чтобы не состарить кадр.
                with shared_numbers().lock:
                    hp, mp, target = self._observe()
            else:
                hp, mp, target = self._observe()
            if target is not None:
                self.target_pending = target > 0
            if hp is not None and mp is not None:
                return hp, mp, target
            self.report(f"HP/MP не распознаны: попытка {attempt + 1}/{self.config.observation_attempts}")
            if attempt + 1 < self.config.observation_attempts:
                # Первый повтор без движения; сидящего персонажа не сдвигаем.
                if attempt >= 1 and self.config.observation_movement and not self.sitting:
                    self.press("WASD"[(attempt - 1) % 4])
                self.controller.wait(self.config.observation_delay)
        raise RuntimeError("HP/MP не распознаны после повторных попыток; фарм остановлен")

    def _observe(self) -> tuple[float | None, float | None, float | None]:
        self.controller.checkpoint()
        if self.clock() >= self.deadline:
            raise RuntimeError("Достигнут лимит времени фарма")
        while True:
            generation = self.controller.pause_generation
            image = self.controller.capture()
            started = self.clock()
            self.controller.checkpoint()
            if generation == self.controller.pause_generation:
                break
        values = []
        for name in ("hp", "mp", "target_hp"):
            if isinstance(self.profile, BarProfile):
                value, source = self.profile.read_detail(image, name)
                self.observation_details[name] = source
            else:
                value = self.profile.read(image, name)
            values.append(value)
        hp, mp, target = values
        if self.clock() - started > 1:
            raise RuntimeError("Наблюдение устарело; фарм остановлен")
        if hp == 0:
            raise RuntimeError("Собственное HP = 0; фарм остановлен")
        return hp, mp, target

    def press(self, key: str) -> None:
        self.controller.key(key, 100)

    def care(self) -> bool:
        hp, mp, target = self.observe()
        if target is not None:
            self.target_pending = target > 0
        changed = False
        heals = 0
        care_deadline = self.clock() + self.config.rest_timeout
        recovering = mp < self.config.mp_threshold
        next_log = self.clock()
        while True:
            # Свежий результат проверяем до таймаута, включая последний кадр у deadline.
            if hp >= self.config.hp_threshold and self.target_pending:
                return changed
            if hp >= self.config.hp_threshold and (not recovering or mp >= self.config.mp_resume):
                if recovering:
                    if self.sitting:
                        self.press(self.config.rest)
                        self.sitting = False
                        self.controller.wait(.5)
                    self.report(f"MP {mp:.1%}: отдых завершён")
                return changed
            now = self.clock()
            details = (f"HP {hp:.1%}; MP {mp:.1%} "
                       f"[{self.observation_details.get('mp', 'источник неизвестен')}]; "
                       f"порог MP {self.config.mp_resume:.1%}; "
                       f"осталось {max(0, care_deadline - now):.1f} с")
            if now >= care_deadline:
                logger.warning("Таймаут восстановления: %s", details)
                raise RuntimeError("Восстановление не завершилось за отведённое время: " + details)
            if now >= next_log:
                self.report("Восстановление: " + details)
                next_log = now + 2
            if hp < self.config.hp_threshold:
                if heals >= self.config.heal_attempts:
                    raise RuntimeError("Лечение не восстановило HP; фарм остановлен")
                if self.sitting:
                    self.press(self.config.rest)
                    self.sitting = False
                    self.controller.wait(.5)
                self.report(f"HP {hp:.0%}: лечение себя")
                self.press(self.config.heal)
                heals += 1
                changed = True
                self.controller.wait(self.config.heal_delay)
                hp, mp, target = self.observe()
                if target is not None:
                    self.target_pending = target > 0
                recovering = recovering or mp < self.config.mp_threshold
                continue
            if not self.sitting:
                self.report(f"MP {mp:.0%}: отдых")
                self.press(self.config.rest)
                self.sitting = True
                changed = True
            self.controller.wait(.3)
            hp, mp, target = self.observe()
            if target is not None:
                self.target_pending = target > 0

    def guarded_wait(self, seconds: float) -> None:
        if self.config.humanize:
            seconds += random.uniform(0, min(.1, seconds * .1))
        deadline = self.clock() + seconds
        while self.clock() < deadline:
            self.controller.wait(min(.3, deadline - self.clock()))
            self.care_target()

    def care_target(self) -> None:
        if self.care():
            raise ReacquireTarget()

    def run(self) -> None:
        for cycle in range(self.config.cycles):
            self.care()
            self.report(f"Цикл {cycle + 1}: next_target")
            try:
                self.fight()
            except ReacquireTarget:
                self.report("После лечения/отдыха выбираем цель заново")

    def fight(self) -> None:
        target = None
        for _ in range(self.config.target_attempts):
            self.press(self.config.next_target)
            self.guarded_wait(self.config.target_delay)
            _, _, target = self.observe()
            if target is not None and target > 0:
                break
        if target is None or target == 0:
            raise RuntimeError("HP цели не найдено; проверьте цель и область полосы")
        deadline = self.clock() + self.config.combat_timeout
        progress_hp, progress_at = target, self.clock()
        reposition_count = 0
        attacks = 0
        while target > 0:
            if self.clock() >= deadline:
                raise RuntimeError("Цель не добита за отведённое время")
            self.care_target()
            _, mp, target = self.observe()
            if target is None or target == 0:
                break
            # Накопленное снижение отделяем от мелкого дрожания цветового parser.
            if target < progress_hp and progress_hp - target >= self.config.progress_epsilon:
                progress_hp, progress_at = target, self.clock()
                reposition_count = 0
            if attacks and self.clock() - progress_at >= self.config.stuck_timeout:
                if reposition_count >= self.config.reposition_attempts:
                    raise RuntimeError("HP цели не снижается после попыток сближения; фарм остановлен")
                reposition_count += 1
                self.report(
                    f"HP цели не снижается {self.config.stuck_timeout:g} с: "
                    f"сближение клавишей {self.config.melee}, попытка {reposition_count}"
                )
                self.press(self.config.melee)
                self.guarded_wait(self.config.reposition_delay)
                _, _, target = self.observe()
                if target is None:
                    raise RuntimeError("HP цели неизвестно после сближения; фарм остановлен")
                # Даём новой позиции время на атаку; общий combat_timeout не продлеваем.
                if target < progress_hp and progress_hp - target >= self.config.progress_epsilon:
                    reposition_count = 0
                progress_hp, progress_at = target, self.clock()
                continue
            finishing = target <= self.config.target_threshold or mp < self.config.mp_threshold
            self.press(self.config.melee if finishing else self.config.attack)
            attacks += 1
            self.guarded_wait(self.config.approach_delay if finishing else self.config.attack_delay)
            _, _, target = self.observe()
            if target is None:
                raise RuntimeError("HP цели исчезло; смерть не подтверждена, фарм остановлен")
        if target is None:
            raise RuntimeError("HP цели неизвестно")
        self.care_target()
        self.report("HP цели = 0 подтверждено надписью: переходим к луту")
        if self.config.pickup_loot:
            self.report("Попытки подбора лута")
            for _ in range(self.config.loot_attempts):
                self.care_target()
                self.press(self.config.loot)
                self.guarded_wait(self.config.loot_delay)
