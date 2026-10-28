"""
Детерминированный цикл фарма с приоритетом собственного HP и MP.
"""

import logging
import json
import math
import random
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hid_pilot.bars import BarProfile
from hid_pilot.controller import ActionController
from hid_pilot.mob_statistics import MobStatistics
from hid_pilot.monster_catalogue import MonsterCatalogue
from hid_pilot.party import PartyProfile, read_party
from hid_pilot.party_statistics import format_statistics
from hid_pilot.paths import user_data_dir
from hid_pilot.resurrection import wait_for_resurrection
from hid_pilot.script import KEYS
from hid_pilot.target import TargetReader

logger = logging.getLogger("hid_pilot.farm")


def prepare_extension(base: Path, args: tuple[str, ...]) -> None:
    profile = BarProfile.model_validate_json((base / args[0]).read_text("utf-8"))
    if not {"hp", "mp", "target_hp"} <= profile.bars.keys() | profile.numbers.keys():
        raise ValueError("Сначала настройте Моё HP, Моё MP и HP цели")
    if (profile.numbers or profile.target_panel or profile.target_player_panel
            or profile.target_level or profile.own_level or (base / "party.json").exists()):
        # Импорт и загрузка OCR могут задержать heartbeat; готовим их до подключения и ARM Pico.
        from hid_pilot.numbers import shared_numbers

        logger.info("Подготовка OCR фарма перед подключением Pico")
        shared_numbers().prepare()
        if (profile.target_panel or profile.target_player_panel or profile.target_level
                or profile.own_level or (base / "party.json").exists()):
            from hid_pilot.farm_observer import background_numbers

            background_numbers().prepare()


def run_extension(controller: ActionController, base: Path, args: tuple[str, ...],
                  config: "FarmConfig", notify: Callable[[str], None], deadline: float) -> None:
    profile = BarProfile.model_validate_json((base / args[0]).read_text("utf-8"))
    remaining = max(0., deadline - time.monotonic())
    if remaining <= 0:
        raise RuntimeError("Достигнут лимит сценария")
    config = config.model_copy(update={"max_seconds": min(config.max_seconds, remaining)})
    farmer = Farmer(controller, profile, config, notify)
    farmer.setup_observation(base)
    try:
        farmer.run()
    finally:
        farmer.close_observation()


class ReacquireTarget(Exception):
    pass


class FarmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    humanize: bool = True
    random_movement: bool = False
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
    cycles: int = Field(default=1000, ge=1, le=10000)
    max_seconds: float = Field(default=10800, ge=1, le=360000)
    max_hours: float = Field(default=3, gt=0, le=100)

    @model_validator(mode="before")
    @classmethod
    def duration_in_hours(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        values = data.copy()
        if "max_hours" in values:
            seconds = float(values["max_hours"]) * 3600
            if "max_seconds" in values and not math.isclose(float(values["max_seconds"]), seconds):
                raise ValueError("max_hours и max_seconds задают разное время; оставьте один параметр")
            values["max_seconds"] = seconds
        elif "max_seconds" in values:
            values["max_hours"] = float(values["max_seconds"]) / 3600
        return values

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
        if isinstance(profile, BarProfile) and (profile.numbers or profile.target_panel or profile.target_player_panel):
            from hid_pilot.numbers import shared_numbers
            shared_numbers().prepare()
        self.config = config
        self.notify = notify
        self.clock = controller.active_time if clock is time.monotonic else clock
        self.deadline = self.clock() + config.max_seconds
        self.next_random_move = self.clock() + random.uniform(30, 60)
        self.sitting = False
        self.target_pending = False
        self.observation_details: dict[str, str] = {}
        self.mob_statistics = MobStatistics(self.clock())
        self.target_reader = TargetReader(profile) if isinstance(profile, BarProfile) else None
        self.last_target = None
        self.target_log_signature: tuple | None = None
        self.own_cp: float | None = None
        self.party_profile: PartyProfile | None = None
        self.current_action = "Фарм"
        self.identity_cache = None
        self.observation_generation = controller.pause_generation
        self.display_data: tuple | None = None
        self.observation_failure = ""
        self.observation_frame: np.ndarray | None = None
        self.observation_recovering = False
        self.observation_timings: dict[str, float] = {}
        self.catalogue: MonsterCatalogue | None = None
        self.debug_recorder = None

    def setup_observation(self, base: Path) -> None:
        from hid_pilot.observation_debug import ObservationDebugRecorder

        if getattr(self.controller, "debug_capture", False) is True:
            self.debug_recorder = ObservationDebugRecorder(user_data_dir() / "observation-debug", self.profile)
            self.report(f"ДебагСтарт: сохранение ошибок цели и уровней включено: {self.debug_recorder.directory}")
        path = base / "party.json"
        if path.exists():
            self.party_profile = PartyProfile.model_validate_json(path.read_text("utf-8"))
        self.catalogue = MonsterCatalogue(user_data_dir() / "monsters.sqlite3")
        self.mob_statistics.database = self.catalogue.path
        from hid_pilot.farm_observer import FarmIdentityCache
        self.identity_cache = FarmIdentityCache(self.profile, self.party_profile, self.catalogue)
        if self.profile.target_panel is None and self.profile.target_player_panel is None:
            self.report("Область имени цели не настроена: убийства учитываются без имени; для /target и оценки урона настройте панель цели")
        if self.profile.target_level is None:
            self.report("Область уровня цели не настроена: выделите цифры уровня в ручной разметке статов")

    def close_observation(self) -> None:
        if self.debug_recorder is not None:
            self.debug_recorder.close()
        if self.identity_cache is not None:
            self.identity_cache.close()

    def invalidate_target(self) -> None:
        self.mob_statistics.reset_target()
        self.last_target = None
        self.target_log_signature = None
        if self.identity_cache is not None:
            self.identity_cache.invalidate()

    def report(self, text: str) -> None:
        self.current_action = text
        logger.info(text)
        self.notify(text)

    def target_description(self) -> str:
        """
        Описывает текущее распознавание без подстановки прежнего имени или HP.
        """
        observation = self.last_target
        if observation is None:
            return "имя неизвестно; тип unknown; класс неизвестно; клан неизвестно; HP неизвестно"
        hp = f"{observation.hp:.1%}" if observation.hp is not None else "неизвестно"
        return (f"имя «{observation.name or 'неизвестно'}»; тип {observation.kind}; "
                f"класс «{observation.player_class or 'неизвестно'}»; "
                f"клан «{observation.clan or 'неизвестно'}»; HP {hp}; "
                f"OCR «{observation.raw_name or observation.name or 'неизвестно'}»; "
                f"подтверждений {observation.name_confirmations}; "
                f"confidence {observation.confidence:.0%}; смерть {observation.dead}")

    def log_target_observation(self) -> None:
        """
        Пишет смену идентичности и доступности HP, без спама каждого процента.
        """
        observation = self.last_target
        signature = ((observation.name, observation.raw_name, observation.kind, observation.player_class, observation.clan,
                      observation.hp is not None, observation.dead) if observation is not None else ())
        if signature != self.target_log_signature:
            logger.info("Распознана цель: %s", self.target_description())
            self.target_log_signature = signature

    def observe(self) -> tuple[float, float, float | None]:
        recovering = False
        successes = 0
        recovery_started = 0.
        recovery_attempts = math.ceil(60 / self.config.observation_delay)
        for attempt in range(self.config.observation_attempts + recovery_attempts):
            self.observation_recovering = recovering
            self.observation_failure = ""
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
                successes += 1
                if recovering and successes < 3:
                    self.controller.wait(self.config.observation_delay)
                    continue
                if recovering:
                    self.observation_recovering = False
                    self.report("Наблюдение восстановлено: 3 свежих кадра HP/MP подряд")
                return hp, mp, target
            successes = 0
            reason = self.observation_failure or "; ".join(
                f"{name.upper()} неизвестно ({self.observation_details.get(name, 'источник неизвестен')})"
                for name, value in (("hp", hp), ("mp", mp)) if value is None)
            if attempt == 0:
                self.controller.release_inputs()
            if attempt == min(2, self.config.observation_attempts - 1):
                self.save_observation_failure(reason)
            if not recovering:
                self.report(f"HP/MP не распознаны: попытка {attempt + 1}/{self.config.observation_attempts}; {reason}")
            elif (attempt - self.config.observation_attempts) % 10 == 0:
                self.report(f"Ожидание восстановления HP/MP: {reason}")
            if not recovering and attempt + 1 >= self.config.observation_attempts:
                recovering = True
                recovery_started = self.clock()
                self.invalidate_target()
                self.own_cp = None
                self.target_pending = False
                self.display_data = None
                self.report("Наблюдение потеряно: ввод отпущен; до 60 с ждём 3 свежих кадра без WASD")
            if recovering and self.clock() - recovery_started >= 60:
                break
            self.controller.wait(self.config.observation_delay)
        raise RuntimeError("HP/MP не распознаны после ожидания восстановления; фарм остановлен")

    def save_observation_failure(self, reason: str) -> None:
        """
        Сохраняет один перезаписываемый комплект диагностики выбранного окна.
        """
        if getattr(self.controller, "debug_capture", False) is not True:
            return
        if not isinstance(self.observation_frame, np.ndarray) or not isinstance(self.profile, BarProfile):
            return
        import cv2

        try:
            directory = user_data_dir() / "observation-failure"
            directory.mkdir(parents=True, exist_ok=True)
            images = {"window": self.observation_frame}
            for name in ("hp", "mp"):
                box = self.profile.numbers.get(name) or self.profile.bars.get(name)
                if box is not None:
                    images[name] = self.profile.crop(self.observation_frame, box)
            for name, frame in images.items():
                ok, encoded = cv2.imencode(".png", frame)
                if ok:
                    encoded.tofile(directory / f"{name}.png")
            (directory / "reason.json").write_text(json.dumps(
                {"reason": reason, "sources": self.observation_details,
                 "read_seconds": self.observation_timings,
                 "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False, indent=2), "utf-8")
            self.report(f"Диагностика HP/MP сохранена: {directory}")
        except (OSError, cv2.error):
            logger.exception("Не удалось сохранить диагностику HP/MP")

    def _observe(self, resources_only: bool = False) -> tuple[float | None, float | None, float | None]:
        self.controller.checkpoint()
        if self.clock() >= self.deadline:
            raise RuntimeError("Достигнут лимит времени фарма")
        while True:
            generation = self.controller.pause_generation
            image = self.controller.capture()
            self.observation_frame = image if getattr(self.controller, "debug_capture", False) is True else None
            started = self.clock()
            self.controller.checkpoint()
            if generation == self.controller.pause_generation:
                break
        values = []
        self.observation_details.clear()
        self.observation_timings.clear()
        for name in (("hp", "mp") if resources_only else ("hp", "mp", "target_hp")):
            read_started = self.clock()
            if isinstance(self.profile, BarProfile):
                value, source = self.profile.read_detail(image, name)
                self.observation_details[name] = source
            else:
                value = self.profile.read(image, name)
            values.append(value)
            self.observation_timings[name] = round(self.clock() - read_started, 3)
        hp, mp = values[:2]
        target = None if resources_only else values[2]
        if self.clock() - started > 1:
            self.observation_failure = f"Чтение ресурсов заняло {self.clock() - started:.2f} с, лимит свежести 1 с"
            if not resources_only:
                self.report(f"{self.observation_failure}; время областей: {self.observation_timings}; повторяем только HP/MP")
                self.invalidate_target()
                return self._observe(resources_only=True)
            return None, None, None
        if hp == 0:
            self.sitting = False
            self.target_pending = False
            self.invalidate_target()
            self.on_own_death()
            return self._observe()
        if resources_only:
            self.own_cp = None
            self.display_data = None
            if generation != self.controller.pause_generation:
                self.observation_failure = "Пауза или смена состояния во время чтения ресурсов"
                return None, None, None
            if not self.observation_recovering:
                self.on_observation(image, hp, mp, None)
            return hp, mp, None
        if self.target_reader is not None:
            if self.identity_cache is not None:
                if generation != self.observation_generation:
                    self.invalidate_target()
                    self.observation_generation = generation
                observation, level, members = self.identity_cache.snapshot(image, started)
            else:
                observation = self.target_reader.read(image, started)
            # Фоновый кеш отдаёт только идентичность: смерть проверяем на текущем кадре.
            marker = bool(self.profile.dead_marker_visible(image))
            observation = observation.model_copy(update={
                "death_marker": marker,
                "dead": self.target_reader.death(
                    target if target is not None else observation.hp, marker)})
            if target is not None:
                # Настроенная область HP приоритетна; OCR имени не должен скрывать её результат.
                observation = observation.model_copy(update={
                    "hp": target, "dead": self.target_reader.death(target, observation.death_marker)})
            elif observation.hp is not None:
                target = observation.hp
                self.observation_details["target_hp"] = "OCR панели цели"
            if self.clock() - started > 1:
                self.report(f"OCR цели задержал кадр до {self.clock() - started:.2f} с: повторяем HP/MP на новом кадре")
                self.invalidate_target()
                return self._observe(resources_only=True)
            self.last_target = observation
        if isinstance(self.profile, BarProfile):
            self.own_cp = self.profile.read(image, "cp") if "cp" in self.profile.bars.keys() | self.profile.numbers.keys() else None
            if self.identity_cache is None:
                level = self.profile.read_level(image, "own_level")
                members = []
                if self.party_profile is not None:
                    from hid_pilot.numbers import shared_numbers
                    members = read_party(image, self.party_profile, shared_numbers())
            if self.clock() - started > 1:
                self.report(f"Дополнительные статы задержали кадр до {self.clock() - started:.2f} с: повторяем HP/MP на новом кадре")
                self.invalidate_target()
                return self._observe(resources_only=True)
            if self.last_target is not None:
                self.mob_statistics.observe(self.last_target, self.clock())
            selected = self.last_target.model_dump() if self.last_target is not None else {"hp": target}
            self.display_data = ({"hp": hp, "mp": mp, "cp": self.own_cp, "level": level}, selected, members)
            if self.profile.summon is not None:
                from hid_pilot.summon import read_summon
                self.display_data[0].update(read_summon(image, self.profile))
            self.publish_statistics()
        if generation != self.controller.pause_generation:
            self.observation_failure = "Пауза или смена состояния во время наблюдения"
            return None, None, None
        self.log_target_observation()
        if self.debug_recorder is not None:
            party_at = getattr(self.identity_cache, "party_at", None)
            party_age = (round(max(0., time.monotonic() - party_at), 3)
                         if isinstance(party_at, (int, float)) else None)
            self.debug_recorder.observe(image, {
                "hp": hp, "mp": mp, "own_level": level,
                "target": self.last_target.model_dump(mode="json") if self.last_target else {"hp": target},
                "sources": self.observation_details.copy(),
                "party": [member.model_dump(mode="json") for member in members],
                "party_profile": self.party_profile.model_dump(mode="json") if self.party_profile else None,
                "party_ocr_age_seconds": party_age,
                "read_seconds": self.observation_timings.copy(), "action": self.current_action})
        if not self.observation_recovering:
            self.on_observation(image, hp, mp, target)
        return hp, mp, target

    def on_own_death(self) -> None:
        wait_for_resurrection(self.controller, self.profile, self.deadline, self.notify, self.clock)

    def on_observation(self, image: np.ndarray, hp: float | None,
                       mp: float | None, target: float | None) -> None:
        """
        Позволяет расширению обработать тот же свежий кадр без повторного capture.
        """

    def publish_statistics(self) -> None:
        if self.display_data is None:
            return
        own, selected, members = self.display_data
        text = format_statistics(own, selected, members, {}, self.current_action)
        text += "\n" + self.mob_statistics.summary(self.clock())
        if callable(getattr(self, "damage_status", None)):
            text += "\n" + self.damage_status()
        known = getattr(self, "known_mobs", {})
        if known:
            text += "\nИзвестные мобы: " + ", ".join(
                f"{name} (ур. {level if level is not None else '?'})"
                for name, level in list(known.items())[:10])
        if callable(getattr(self.notify, "state", None)):
            self.notify.state({"text": text})

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
            self.try_random_movement()
            self.report(f"Цикл {cycle + 1}: next_target")
            try:
                self.fight()
            except ReacquireTarget:
                self.report("После лечения/отдыха выбираем цель заново")

    def try_random_movement(self) -> bool:
        if not self.config.random_movement or self.clock() < self.next_random_move or self.sitting:
            return False
        generation = self.controller.pause_generation
        hp, mp, _ = self.observe()
        if hp is None or mp is None or hp < self.config.hp_threshold or mp < self.config.mp_threshold:
            return False
        if generation != self.controller.pause_generation:
            self.next_random_move = self.clock() + random.uniform(30, 60)
            return False
        duration = random.randint(300, 3000 if self.controller.transport.protocol == "HID_PILOT_V4" else 1000)
        if self.clock() + duration / 1000 + .02 > self.deadline:
            return False
        key = random.choice(("W", "A", "S", "D"))
        self.controller.key(key, duration)
        self.next_random_move = self.clock() + random.uniform(30, 60)
        self.invalidate_target()
        self.report(f"Случайное движение: {key}, {duration} мс")
        return True

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
            if self.last_target is not None and self.catalogue is None:
                self.mob_statistics.remember(self.last_target)
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
