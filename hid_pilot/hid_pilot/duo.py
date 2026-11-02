"""
Общий детерминированный сценарий МА и саппорта с одним Pico.
"""

from collections.abc import Callable
from collections import deque
from pathlib import Path
import json
import shlex

import numpy as np
from pydantic import Field, field_validator, model_validator

from hid_pilot.bars import BarProfile, bar_fraction
from hid_pilot.party import PartyMember, PartyProfile
from hid_pilot.controller import ActionController
from hid_pilot.desktop import Target
from hid_pilot.support_life import SupportLifeObserver, empty_hp_bar
from hid_pilot.summon import read_summon
from hid_pilot.summon_recovery import SummonRecovery
from hid_pilot.script import KEYS, script_variables
from hid_pilot.farm_melee import INACTIVE_MELEE_SETTINGS, MeleeConfig, MeleeFarmer, prepare_extension

PARTY_REGEN_SECONDS = 15.
PARTY_REGEN_RETRIES = 3
SUPPORT_SKILL_REPEAT_SECONDS = .085
STRONG_AGGRESSOR_SELECTIONS = 6
SUMMON_STALL_SECONDS = 180.
SUMMON_MIN_MP = .1
SUMMON_MAX_ATTEMPTS = 10

class DuoConfig(MeleeConfig):
    summon_key: str | None = "NUM6"
    summon_cast_seconds: float = Field(default=20, ge=.1, le=120)
    summon_cooldown: float = Field(default=5, ge=.1, le=300)
    ud_key: str | None = None
    ud_cooldown: float = Field(default=900, ge=1, le=86400)
    ud_damage_per_second: float = Field(default=.05, gt=0, le=1)
    ud_hp_threshold: float = Field(default=.07, gt=0, lt=1)
    mass_aggro_key: str | None = None
    mass_aggro_cast_seconds: float = Field(default=1, ge=.1, le=30)
    mass_aggro_cooldown: float = Field(default=40, ge=40, le=86400)
    mass_aggro_hp_threshold: float = Field(default=.33, gt=0, lt=1)
    mass_aggro_damage_per_second: float = Field(default=.15, gt=0, le=1)
    support_buff_min_mp: float = Field(default=.6, gt=0, le=1)
    support_buff_emergency_hp: float = Field(default=.35, gt=0, lt=1)
    support_mass_buff_enabled: bool = True
    support_mass_buff3_key: str | None = "NUM3"
    support_mass_buff4_key: str | None = "NUM4"
    support_mass_buff5_key: str | None = "NUM5"
    support_mass_buff6_key: str | None = "NUM6"
    support_mass_buff7_key: str | None = "NUM7"
    support_mass_buff8_key: str | None = "NUM8"
    support_mass_buff9_key: str | None = "NUM9"
    support_mass_buff0_key: str | None = "NUM0"
    support_mass_buff_divide_key: str | None = "NUMDIVIDE"
    support_mass_buff_multiply_key: str | None = "NUMMULTIPLY"
    support_mass_buff_cast_seconds: float = Field(default=1, ge=.1, le=60)
    support_mass_buff_min_mp: float = Field(default=.09, gt=0, le=1)
    rest: str = "="
    heal: str = "F2"
    buff_key: str = "F1"
    support_buff2_key: str | None = None
    follow_key: str = "F2"
    support_key: str = "F3"
    support_assist_retry_seconds: float = Field(default=5, ge=2, le=30)
    support_assist_retries: int = Field(default=1, ge=0, le=3)
    heal_key: str | None = None
    support_self_heal_key: str | None = None
    support_potion_key: str | None = None
    support_potion_hp_threshold: float = Field(default=.5, gt=0, le=1)
    support_potion_cooldown: float = Field(default=2, ge=.1, le=300)
    support_hp_elixir_key: str | None = "NUM2"
    support_hp_elixir_hp_threshold: float = Field(default=.5, gt=0, le=1)
    support_hp_elixir_cooldown: float = Field(default=300, ge=300, le=86400)
    support_vampiric_key: str | None = None
    support_vampiric_hp_threshold: float = Field(default=.6, gt=0, le=1)
    support_vampiric_cast_seconds: float = Field(default=1, ge=.1, le=30)
    support_vampiric_cooldown: float = Field(default=5, ge=.1, le=300)
    support_party_heal_key: str | None = None
    support_party_heal_threshold: float = Field(default=.6, gt=0, le=1)
    support_party_heal_cast_seconds: float = Field(default=1, ge=.1, le=30)
    support_party_heal_cooldown: float = Field(default=5, ge=.1, le=300)
    support_lifeimpact_key: str | None = "9"
    support_lifeimpact_cast_seconds: float = Field(default=1, ge=.1, le=30)
    support_lifeimpact_cooldown: float = Field(default=15, ge=15, le=86400)
    support_lifeimpact_hp_threshold: float = Field(default=.7, gt=0, lt=1)
    support_lifeimpact_emergency_hp: float = Field(default=.2, gt=0, lt=1)
    support_lifeimpact_regen_remaining: float = Field(default=10, gt=0, le=15)
    support_preserv_key: str | None = "="
    support_preserv_cast_seconds: float = Field(default=1, ge=.1, le=30)
    support_preserv_cooldown: float = Field(default=20, ge=20, le=86400)
    support_preserv_hp_threshold: float = Field(default=.47, gt=0, lt=1)
    support_preserv_damage_per_second: float = Field(default=.05, gt=0, le=1)
    stop_attack_key: str = "ESC"
    defense_target_key: str = "3"
    buff_seconds: float = Field(default=10, ge=1, le=60)
    support_buff2_seconds: float = Field(default=10, ge=1, le=60)
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
        duration = self.buff_seconds + (self.support_buff2_seconds if self.support_buff2_key else 0)
        if self.rebuff_minutes * 60 <= duration:
            raise ValueError("Интервал ребаффа должен быть больше длительности баффа")
        return self

    @field_validator("support_mass_buff3_key", "support_mass_buff4_key", "support_mass_buff5_key",
                     "support_mass_buff6_key", "support_mass_buff7_key", "support_mass_buff8_key",
                     "support_mass_buff9_key", "support_mass_buff0_key", "support_mass_buff_divide_key",
                     "support_mass_buff_multiply_key",
                     "summon_key", "support_buff2_key", "ud_key", "mass_aggro_key", "heal_key", "support_self_heal_key", "support_potion_key",
                     "support_vampiric_key", "support_party_heal_key", "support_preserv_key",
                     "support_lifeimpact_key")
    @classmethod
    def optional_heal_key(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        key = value.strip()
        if key not in KEYS:
            raise ValueError(f"Клавиша {key} не поддерживается")
        return key

    @field_validator("support_hp_elixir_key")
    @classmethod
    def support_elixir_key(cls, value: str | None) -> str | None:
        return cls.optional_elixir_key(value)

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
    lines = ["# Дуо-фарм: МА + саппорт.",
             "# START запускается с вкладки «Дуо: МА + саппорт».",
             '# Пати-реген отключается строкой: set support_party_heal_key ""', ""]
    for name, value in config.model_dump(mode="json").items():
        if name == "max_seconds" or name in INACTIVE_MELEE_SETTINGS:
            # Один параметр времени исключает конфликт часов и секунд при правке.
            continue
        if name == "buff_key":
            lines.extend(["", "# Саппорт во втором окне."])
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
        raise ValueError("Настройте HP/MP МА и HP цели на вкладке первого окна")
    left, top, right, bottom = farmer.rect
    if (profile.width, profile.height) != (right - left, bottom - top):
        raise ValueError("Размер окна МА изменился; повторите разметку")
    prepare_extension(base / "farmer", ("farm-bars.json",))
    return profile


def load_duo_party(base: Path, farmer: Target) -> PartyProfile:
    path = base / "farmer" / "party.json"
    if not path.exists():
        raise ValueError("Настройте пати в окне МА для наблюдения HP саппорта")
    profile = PartyProfile.model_validate_json(path.read_text("utf-8"))
    left, top, right, bottom = farmer.rect
    if (profile.width, profile.height) != (right - left, bottom - top):
        raise ValueError("Размер профиля пати не совпадает с окном МА")
    return profile


class Rebuffed(Exception):
    pass


class SupportDied(Exception):
    pass


class DuoFarmer(MeleeFarmer):
    config: DuoConfig

    def __init__(self, controller: ActionController, farmer: Target, support: Target,
                 profile: BarProfile, config: DuoConfig, notify: Callable[[str], None],
                 observation: Callable[[dict], None] | None = None,
                 party_profile: PartyProfile | None = None,
                 support_life: SupportLifeObserver | None = None) -> None:
        if farmer.hwnd == support.hwnd:
            raise ValueError("Выберите два разных окна")
        if controller.transport.protocol != "HID_PILOT_V4":
            raise RuntimeError("Дуо мили-фарм требует прошивки HID_PILOT_V4; ввод не начат")
        super().__init__(controller, profile, config, notify)
        self.farmer = farmer
        self.support = support
        self.support_life = support_life
        self.support_lost = False
        self.summon_recovery = SummonRecovery()
        self.summon_servicing = False
        self.summon_casting = False
        self.summon_defending = False
        self.observation = observation or (lambda payload: None)
        self.last_heal = float("-inf")
        self.support_heal_ready: dict[str, float] = {}
        self.heals = 0
        self.self_heals = 0
        self.support_potion_ready = float("-inf")
        self.support_hp_elixir_ready = float("-inf")
        self.support_vampiric_ready = float("-inf")
        self.support_vampiric_recovering = False
        self.support_vampiric_generation = controller.pause_generation
        self.support_vampiric_attempts = 0
        self.support_vampiric_verify = False
        self.support_party_heal_ready = float("-inf")
        self.support_preserv_ready = float("-inf")
        self.support_party_regen_until = float("-inf")
        self.support_ma_chant_until = float("-inf")
        self.support_ma_chant_generation = controller.pause_generation
        self.support_lifeimpact_ready = float("-inf")
        self.support_party_heal_attempts = 0
        self.party_heal_status_at = float("-inf")
        self.party_profile = party_profile
        self.next_buff = self.clock() + config.buff_delay_minutes * 60
        self.buffing = False
        self.support_buff_until: float | None = None
        self.support_buff_stage = 0
        self.support_buff_started = 0.
        self.buff_mp_reason = ""
        self.mass_buff_index = 0
        self.mass_buff_active = False
        self.post_summon_mass_buff = False
        self.mass_buff_ready = float("-inf")
        self.mass_buff_generation = controller.pause_generation
        self.previous_resources: dict[str, float | None] = {}
        self.resource_generation = controller.pause_generation
        self.resources_at = float("-inf")
        self.damage_until = float("-inf")
        self.party_damage_now = False
        self.party_damage_sources: tuple[str, ...] = ()
        self.party_hp_damage_rates: dict[str, float] = {}
        self.summon_damage_rate: float | None = None
        self.summon_damage_until = float("-inf")
        self.strong_damage_until = float("-inf")
        self.strong_aggressor: tuple[str, int | None, int] | None = None
        self.strong_aggressor_selections = 0
        self.strong_aggressor_allowed: tuple[str, int | None, int] | None = None
        self.strong_aggressor_pending = False
        self.strong_aggressor_generation = controller.pause_generation
        self.mass_aggro_ready_at = float("-inf")
        self.mass_aggro_casting = False
        self.support_damage_until = float("-inf")
        self.support_damage_rate: float | None = None
        self.support_status_at = float("-inf")
        self.support_rescue_until = float("-inf")
        self.support_rescue_generation = controller.pause_generation
        self.support_pending = False
        self.support_assist_attempts = 0
        self.support_assist_retry_at = float("inf")
        self.support_assist_identity: tuple[str, int | None] | None = None
        self.support_assist_generation = controller.pause_generation
        self.following = False
        self.escaping = False
        self.follow_generation = controller.pause_generation
        self.attacking = False
        self.reset_support_attack()
        self.ud_ready_at = float("-inf")
        self.ud_hp_samples: deque[tuple[float, float]] = deque(maxlen=128)
        self.ud_damage_rate: float | None = None
        self.ud_generation = controller.pause_generation
        self.ud_confirmations = 0
        self.ud_confirmation_at: float | None = None
        self._ud_observing = False
        self.report(f"Лечение саппорта: строка пати {config.support_party_slot}; "
                    f"эликсир {config.support_hp_elixir_key or 'отключён'}, "
                    f"HP <{config.support_hp_elixir_hp_threshold:.0%}, "
                    f"откат {config.support_hp_elixir_cooldown:g} с; "
                    f"банка {config.support_potion_key or 'отключена'}, "
                    f"HP <{config.support_potion_hp_threshold:.0%}, "
                    f"откат {config.support_potion_cooldown:g} с; "
                    f"вампирик {config.support_vampiric_key or 'отключён'}, "
                    f"HP <{config.support_vampiric_hp_threshold:.0%}, "
                    f"откат {config.support_vampiric_cooldown:g} с")
        self.report(f"Пати-реген {config.support_party_heal_key or 'отключён'}: "
                    f"эффект {PARTY_REGEN_SECONDS:g} с, восстановление HP каждую секунду; "
                    f"до {PARTY_REGEN_RETRIES} повторов по откату, двойное нажатие с паузой 85 мс; "
                    f"эликсир МА при HP <{config.hp_elixir_hp_threshold:.0%}")
        self.report(f"LifeImpact {config.support_lifeimpact_key or 'отключён'}: только МА, +15% max HP; "
                    f"HP <{config.support_lifeimpact_hp_threshold:.0%}, остаток ChantOfLife "
                    f"<{config.support_lifeimpact_regen_remaining:g} с, при HP "
                    f"<{config.support_lifeimpact_emergency_hp:.0%} без ожидания; "
                    "наличие баффа определяется расчётным таймером")

    def track_ud_damage(self, hp: float, now: float) -> None:
        if (self.ud_generation != self.controller.pause_generation
                or self.ud_hp_samples and (now - self.ud_hp_samples[-1][0] > 1
                                          or now <= self.ud_hp_samples[-1][0])):
            self.ud_hp_samples.clear()
            self.ud_confirmations = 0
            self.ud_confirmation_at = None
        self.ud_generation = self.controller.pause_generation
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
        if (self.config.ud_key is None or hp <= 0 or self.clock() < self.ud_ready_at
                or self.saving_mana_for_buffs()):
            self.ud_confirmations = 0
            self.ud_confirmation_at = None
            return
        heavy_damage = (self.ud_damage_rate is not None
                        and self.ud_damage_rate > self.config.ud_damage_per_second + 1e-9)
        if hp >= self.config.ud_hp_threshold and not heavy_damage:
            self.ud_confirmations = 0
            self.ud_confirmation_at = None
            return
        # Подтверждения относятся к двум разным свежим наблюдениям HP.
        observed_at = self.ud_hp_samples[-1][0] if self.ud_hp_samples else None
        if observed_at is None or observed_at == self.ud_confirmation_at:
            return
        self.ud_confirmation_at = observed_at
        self.ud_confirmations += 1
        if self.ud_confirmations < 2:
            return
        rate = f"{self.ud_damage_rate:.1%}/с" if self.ud_damage_rate is not None else "неизвестен"
        self.report(f"УД МА: подтверждено 2/2; HP {hp:.1%}, наблюдаемый урон {rate}; "
                    f"{self.config.ud_key}, удержание 1 с")
        self.controller.key(self.config.ud_key, 1000, exact=True)
        self.ud_ready_at = self.clock() + self.config.ud_cooldown
        self.ud_confirmations = 0
        self.ud_confirmation_at = None

    def support_resources(self, image: np.ndarray) -> dict[str, float | None]:
        """
        Читает саппорта из текущего кадра МА; пустое HP видимой строки означает смерть.
        """
        if self.party_profile is None:
            return {}
        slot = self.config.support_party_slot - 1
        hp_image = self.profile.crop(image, self.party_profile.box("hp", slot))
        hp = bar_fraction(hp_image, "hp")
        mp = bar_fraction(self.profile.crop(image, self.party_profile.box("mp", slot)), "mp")
        if hp is None:
            # Нулевая мана не скрывает смерть: CP также подтверждает видимую строку.
            visible = mp is not None
            if not visible:
                cp = bar_fraction(self.profile.crop(image, self.party_profile.box("cp", slot)), "cp")
                visible = cp is not None
            if visible and empty_hp_bar(hp_image):
                hp = 0.
        resources = {"support_hp": hp, "support_mp": mp}
        if not self.support_lost:
            if self.support_life is not None:
                self.support_life.observe_party_hp(hp)
            if hp == 0:
                self.previous_resources.update(resources)
                self.report("Саппорт погиб: пустая HP-полоса в панели пати МА")
                self.lose_support()
        return resources

    def track_damage(self, image: np.ndarray, hp: float) -> None:
        resources: dict[str, float | None] = {"farmer_hp": hp}
        if isinstance(self.profile, BarProfile):
            resources.update(read_summon(image, self.profile))
        if isinstance(self.profile, BarProfile) and "cp" in self.profile.bars.keys() | self.profile.numbers.keys():
            resources["farmer_cp"] = self.profile.read(image, "cp")
        if self.party_profile is not None:
            resources.update(self.support_resources(image))
            if self.display_data is not None:
                # Эти HP/CP уже прочитаны из того же кадра, без дополнительного OCR.
                for member in self.display_data[2]:
                    if isinstance(member, PartyMember):
                        resources[f"party_{member.slot}_hp"] = member.hp
                        resources[f"party_{member.slot}_cp"] = member.cp
        now = self.controller.active_time()
        self.track_ud_damage(hp, now)
        support_hp = resources.get("support_hp")
        self.update_support_recovery(support_hp)
        previous_hp = self.previous_resources.get("support_hp")
        elapsed = now - self.resources_at
        if (self.resource_generation != self.controller.pause_generation
                or not 0 <= elapsed <= 1
                or any(name.endswith("_hp") and previous is not None
                       and (resources.get(name) is None or resources[name] <= 0)
                       for name, previous in self.previous_resources.items())):
            self.strong_damage_until = float("-inf")
            self.reset_strong_aggressor_search()
        elif elapsed > 0:
            for name, value in resources.items():
                previous = self.previous_resources.get(name)
                if (name.endswith("_hp") and value is not None and previous is not None
                        and 0 < value < previous
                        and (previous - value) / elapsed >= self.config.mass_aggro_damage_per_second - 1e-9):
                    self.strong_damage_until = now + self.config.damage_memory_seconds
                    break
        if now >= self.strong_damage_until:
            self.reset_strong_aggressor_search()
        self.party_hp_damage_rates.clear()
        if self.resource_generation == self.controller.pause_generation and 0 < elapsed <= 1:
            for name, value in resources.items():
                previous = self.previous_resources.get(name)
                if (name != "farmer_hp" and name.endswith("_hp")
                        and value is not None and previous is not None and 0 < value < previous):
                    self.party_hp_damage_rates[name] = (previous - value) / elapsed
        summon_hp = resources.get("summon_hp")
        previous_summon_hp = self.previous_resources.get("summon_hp")
        if (self.resource_generation != self.controller.pause_generation
                or not 0 <= elapsed <= 1
                or summon_hp is None or previous_summon_hp is None or summon_hp <= 0
                or previous_summon_hp <= 0
                or summon_hp > previous_summon_hp + self.config.damage_epsilon):
            self.summon_damage_rate = None
            self.summon_damage_until = float("-inf")
        elif "summon_hp" in self.party_hp_damage_rates:
            # Коротко сохраняем наблюдаемый урон, чтобы каст масс-агра не вытеснил Preserv.
            self.summon_damage_rate = self.party_hp_damage_rates["summon_hp"]
            self.summon_damage_until = now + self.config.damage_memory_seconds
        if (self.resource_generation != self.controller.pause_generation
                or not 0 <= elapsed <= 1 or support_hp is None or previous_hp is None
                or support_hp <= 0 or support_hp > previous_hp + self.config.damage_epsilon):
            self.support_damage_until = float("-inf")
            self.support_damage_rate = None
        elif elapsed > 0 and previous_hp - support_hp > self.config.damage_epsilon:
            rate = (previous_hp - support_hp) / elapsed
            # Падение хотя бы на 5% HP/с разрешает один быстрый повтор лечебной команды.
            if rate >= .05:
                self.support_damage_until = now + self.config.damage_memory_seconds
                self.support_damage_rate = rate
        if (self.resource_generation != self.controller.pause_generation
                or now - self.resources_at > 1):
            self.previous_resources.clear()
            self.damage_until = float("-inf")
        damage_sources = []
        for name, value in resources.items():
            if name in {"support_mp", "summon_mp"}:
                continue
            previous = self.previous_resources.get(name)
            if value is not None and previous is not None and previous - value > self.config.damage_epsilon:
                self.damage_until = now + self.config.damage_memory_seconds
                damage_sources.append(name)
        self.party_damage_now = bool(damage_sources)
        self.party_damage_sources = tuple(damage_sources)
        self.previous_resources = resources
        self.resource_generation = self.controller.pause_generation
        self.resources_at = now
        if self.config.summon_key is not None:
            if self.summon_recovery.observe(resources, now, self.controller.pause_generation,
                                            self.party_damage_now, self.config.damage_epsilon):
                reason = ("HP пантеры равно нулю" if resources.get("summon_hp") == 0
                          else "HP/MP пантеры устойчиво не читаются")
                self.report(f"{reason}: назначен призыв; "
                            "текущую цель добиваем, новый поиск ждёт призыва и полного ребаффа")
            if self.summon_casting and (self.party_damage_now or self.critical_party()):
                self.cancel_summon_cast()

    def use_mass_aggro(self) -> bool:
        """
        Прикрывает сопартийцев массовым агром без смены текущей цели МА.
        """
        config = self.config
        if (config.mass_aggro_key is None or self.support_lost or self.mass_aggro_casting
                or self.saving_mana_for_summon()):
            return False
        self.check()
        now = self.controller.active_time()
        own_hp = self.previous_resources.get("farmer_hp")
        if (now < self.mass_aggro_ready_at or self.observation_recovering
                or self.resource_generation != self.controller.pause_generation
                or not 0 <= now - self.resources_at <= 1
                or self.last_mp is None or self.last_mp <= 0
                or own_hp is None or own_hp <= 0
                or self.is_unsafe_target()):
            return False
        reasons = []
        for name, hp in self.previous_resources.items():
            if name == "farmer_hp" or not name.endswith("_hp") or hp is None or hp <= 0:
                continue
            rate = self.party_hp_damage_rates.get(name, 0.)
            if hp < config.mass_aggro_hp_threshold or rate >= config.mass_aggro_damage_per_second - 1e-9:
                label = {"support_hp": "саппорт", "summon_hp": "самон"}.get(name, name)
                reasons.append(f"{label}: HP {hp:.1%}, падение {rate:.1%}/с")
        if not reasons:
            return False
        self.report(f"Масс-агр МА {config.mass_aggro_key}: " + "; ".join(reasons)
                    + f"; удержание 0.3 с, каст {config.mass_aggro_cast_seconds:g} с, "
                    f"откат {config.mass_aggro_cooldown:g} с")
        started = now
        self.controller.key(config.mass_aggro_key, 300, exact=True)
        self.mass_aggro_ready_at = started + config.mass_aggro_cooldown
        self.mass_aggro_casting = True
        try:
            self.wait_with_emergency_checks(max(0., started + config.mass_aggro_cast_seconds
                                               - self.controller.active_time()))
        finally:
            self.mass_aggro_casting = False
        # Отправка скилла не подтверждает, что все монстры уже сменили агрессора.
        return True

    def critical_party(self) -> list[tuple[str, float]]:
        """
        Возвращает критические HP только из свежего наблюдения текущей сессии.
        """
        if (self.resource_generation != self.controller.pause_generation
                or not 0 <= self.controller.active_time() - self.resources_at <= 1):
            return []
        return [(name, hp) for name, hp in self.previous_resources.items()
                if name.endswith("_hp") and hp is not None
                and 0 < hp < self.config.support_buff_emergency_hp]

    def update_support_buff(self) -> bool:
        if not self.buffing or self.support_buff_until is None:
            return False
        now = self.controller.active_time()
        critical = self.critical_party()
        if critical:
            self.visit(self.config.stop_attack_key, .1)
            self.buffing = False
            self.support_buff_until = None
            self.support_buff_stage = 0
            self.next_buff = min(self.next_buff, self.controller.active_time())
            reason = ", ".join(f"{name} {hp:.1%}" for name, hp in critical)
            self.report(f"Критическое HP пати ({reason}): бафф прерван для спасения; "
                        "полный бафф повторим после восстановления")
            return True
        if now >= self.support_buff_until:
            if self.support_buff_stage == 1 and self.config.support_buff2_key:
                self.visit(self.config.support_buff2_key, .5, settle_seconds=.5,
                           key_milliseconds=150)
                self.support_buff_stage = 2
                self.support_buff_until = (self.controller.active_time()
                                           + self.config.support_buff2_seconds)
                self.report(f"Второй бафф саппорта {self.config.support_buff2_key} отправлен")
                return True
            self.buffing = False
            self.support_buff_until = None
            self.next_buff = self.support_buff_started + self.config.rebuff_minutes * 60
            # После баффа заново запускаем ассист и его ограниченный контрольный повтор.
            self.support_assist_attempts = 0
            self.support_assist_retry_at = float("inf")
            self.support_pending = True
            self.report("Макрос саппорта завершён по таймеру; "
                        "если МА в бою с живой целью, отправляем ассист")
        return False

    def cast_buffs(self) -> None:
        if self.support_lost:
            return
        if self.config.summon_key is not None and (not self.summon_recovery.checked
                                                  or self.summon_recovery.pending):
            return
        if (not self.buffing and not self.summon_recovery.rebuff and self.config.support_mass_buff_enabled
                and (self.post_summon_mass_buff or self.mass_buff_active or self.attacking or self.under_attack)
                and self.controller.active_time() >= self.next_buff):
            if self.cast_mass_buff():
                return
            super().cast_buffs()
            return
        if not self.buffing and self.controller.active_time() >= self.next_buff:
            self.buff()
        else:
            super().cast_buffs()

    def cast_mass_buff(self) -> bool:
        """
        Даёт один пати-бафф двойным нажатием и сразу возвращает окно МА.
        """
        generation = self.controller.pause_generation
        if generation != self.mass_buff_generation:
            self.mass_buff_index = 0
            self.mass_buff_active = False
            self.mass_buff_generation = generation
        if self.controller.active_time() < self.mass_buff_ready:
            return False
        hp, _, _ = self.observe()
        if not self.buff_health_safe(hp):
            if self.critical_party():
                self.mass_buff_index = 0
                self.mass_buff_active = False
            return False
        keys = [getattr(self.config, f"support_mass_buff{slot}_key")
                for slot in (3, 4, 5, 6, 7, 8, 9, 0, "_divide", "_multiply")]
        keys = [key for key in keys if key is not None]
        if not keys:
            return False
        mp = self.previous_resources.get("support_mp")
        minimum = (self.config.support_mass_buff_min_mp if self.mass_buff_active or self.post_summon_mass_buff
                   else self.config.support_buff_min_mp)
        if mp is None or mp < minimum:
            return False
        key = keys[self.mass_buff_index]
        sent_at = self.visit(key, .1, double_press=True)
        if sent_at is None or generation != self.controller.pause_generation or self.support_lost:
            return False
        self.mass_buff_generation = generation
        self.mass_buff_active = True
        self.mass_buff_index += 1
        self.mass_buff_ready = self.controller.active_time() + self.config.support_mass_buff_cast_seconds
        self.buff_actions += 1
        self.support_pending = True
        self.report(f"Масс-пати бафф саппорта {self.mass_buff_index}/{len(keys)}: {key}, возврат к фарму")
        if self.mass_buff_index == len(keys):
            self.next_buff = self.controller.active_time() + self.config.rebuff_minutes * 60
            self.mass_buff_index = 0
            self.mass_buff_active = False
            self.post_summon_mass_buff = False
            self.report("Все масс-пати баффы саппорта нажаты; таймер следующего ребаффа запущен")
        return True

    def buff_health_safe(self, hp: float) -> bool:
        if self.summon_recovery.rebuff and not self.summon_recovery.quiet(
                self.clock(), self.config.quiet_seconds, self.controller.pause_generation):
            return False
        return super().buff_health_safe(hp) and not self.critical_party()

    def wait_for_buff(self, seconds: float) -> bool:
        completed = super().wait_for_buff(seconds)
        if not completed and self.critical_party():
            self.controller.key(self.config.stop_attack_key, 80, exact=True)
        return completed

    def buffs_due(self) -> bool:
        if self.support_lost:
            return False
        return (super().buffs_due()
                or not self.buffing and self.controller.active_time() >= self.next_buff)

    def buff(self) -> None:
        hp, _, _ = self.observe()
        if self.buffing or hp <= self.config.buff_min_hp or self.critical_party():
            return
        if self.summon_recovery.rebuff and not self.buff_health_safe(hp):
            return
        support_hp = self.previous_resources.get("support_hp")
        if support_hp is not None and support_hp < self.config.support_buff_emergency_hp:
            return
        mp = self.previous_resources.get("support_mp")
        if mp is None or mp < self.config.support_buff_min_mp:
            reason = "MP саппорта неизвестно" if mp is None else f"MP саппорта {mp:.0%} < {self.config.support_buff_min_mp:.0%}"
            if self.buff_mp_reason != reason:
                self.report(f"Полный бафф отложен: {reason}")
                self.buff_mp_reason = reason
            super().cast_buffs()
            return
        self.buff_mp_reason = ""
        self.report("Полный бафф: подготовка окна саппорта")
        self.visit(self.config.buff_key, .5, settle_seconds=.5, key_milliseconds=150)
        self.support_buff_started = self.controller.active_time()
        self.report(f"Кнопка полного баффа саппорта {self.config.buff_key} отправлена: "
                    "пауза после активации 0.5 с, удержание 150 мс, перед возвратом 0.5 с")
        self.buff_actions += 1
        self.support_buff_until = self.controller.active_time() + self.config.buff_seconds
        self.support_buff_stage = 1
        self.buffing = True
        self.support_pending = True
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

    def wait_for_vampiric(self, seconds: float) -> None:
        """
        Во время собственного каста проверяет аварийные ресурсы МА и пати.
        """
        self.wait_with_emergency_checks(seconds, own_cast=True)

    def wait_with_emergency_checks(self, seconds: float, *, own_cast: bool = False) -> None:
        """
        Наблюдает ресурсы между короткими ожиданиями без рекурсивного запуска скиллов.
        """
        until = self.controller.active_time() + seconds
        generation = self.controller.pause_generation
        while True:
            self.check()
            if self.controller.pause_generation != generation:
                return
            hp, _, _ = self._observe(resources_only=True)
            # on_observation обслуживает эликсир и УД МА на этом же кадре.
            if hp is None:
                return
            party_rescue_ready = (self.buffing or self.config.support_party_heal_key is not None
                                  and self.support_heal_key_ready(self.config.support_party_heal_key))
            if own_cast and self.critical_party() and party_rescue_ready:
                self.controller.key(self.config.stop_attack_key, 80, exact=True)
                self.report("Каст МА прерван: критическое HP пати, возвращаемся к спасению")
                return
            if self.support_consumables():
                # После визита нельзя продолжать по старому кадру.
                self._observe(resources_only=True)
            remaining = until - self.controller.active_time()
            if remaining <= 0:
                return
            self.controller.wait(min(self.config.poll_seconds, remaining))

    def visit(self, key: str, seconds: float, *, settle_seconds: float = 0.,
              key_milliseconds: int = 80, repeat: bool = False, exact: bool = False,
              double_press: bool = False) -> float | None:
        if self.support_lost:
            return
        generation = self.controller.pause_generation
        self.check()
        self.controller.switch_window(self.support)
        self.observe_support_life()
        if self.support_lost:
            return None
        self.following = False
        if settle_seconds:
            # Игра должна успеть принять фокус; wait и key повторно проверяют его.
            self.controller.wait(settle_seconds)
            self.check()
        # При STOP или потере фокуса не перехватываем окно через finally.
        if double_press:
            sent_at = self.send_support_skill(key, generation)
        else:
            self.controller.key(key, key_milliseconds, exact=exact or bool(settle_seconds))
            sent_at = self.controller.active_time()
        if repeat and not double_press:
            self.controller.wait(.15)
            self.check()
            if generation == self.controller.pause_generation:
                self.observe_support_life()
                self.controller.key(key, key_milliseconds, exact=True)
                self.report(f"Аварийный повтор команды саппорта {key}: 2/2, пауза 150 мс")
        self.controller.wait(seconds)
        self.check()
        self.controller.switch_window(self.farmer)
        return sent_at

    def send_support_skill(self, key: str, generation: int) -> float | None:
        """
        Дважды нажимает скилл с паузой 85 мс между отпусканием и нажатием.
        """
        self.check()
        if self.support_lost or generation != self.controller.pause_generation:
            return None
        self.controller.key(key, 80, exact=True)
        sent_at = self.controller.active_time()
        self.controller.wait(SUPPORT_SKILL_REPEAT_SECONDS)
        self.check()
        if generation == self.controller.pause_generation:
            self.observe_support_life()
            if generation == self.controller.pause_generation and not self.support_lost:
                self.controller.key(key, 80, exact=True)
        return sent_at

    def observe_support_life(self) -> None:
        if self.support_life is not None:
            fresh = (self.resource_generation == self.controller.pause_generation
                     and 0 <= self.controller.active_time() - self.resources_at <= 1)
            hp = self.previous_resources.get("support_hp") if fresh else None
            self.support_life.observe(self.controller, hp)
            if self.support_life.dead is True:
                self.controller.switch_window(self.farmer)
                self.lose_support()

    def lose_support(self) -> None:
        self.support_lost = True
        self.buffing = False
        self.support_buff_until = None
        self.support_pending = False
        self.following = False
        self.support_vampiric_recovering = False
        self.support_vampiric_verify = False
        raise SupportDied("Саппорт погиб: добиваем текущую цель и завершаем фарм")

    def on_observation(self, image: np.ndarray, hp: float | None,
                       mp: float | None, target: float | None) -> None:
        # Аварийные скиллы также учитывают MP этого кадра, до возврата из observe.
        self.last_mp = mp
        if self.use_hp_elixir(hp):
            # После мгновенного эликсира не тратим УД по старому низкому HP.
            return
        if hp is None or mp is None:
            self.summon_recovery.invalidate()
            if self.summon_casting:
                self.cancel_summon_cast()
            self.support_resources(image)
            if hp is None:
                self.ud_hp_samples.clear()
                self.ud_damage_rate = None
                self.ud_confirmations = 0
                self.ud_confirmation_at = None
            else:
                # Для аварийного УД достаточно известного HP, распознавание MP не требуется.
                self.track_ud_damage(hp, self.controller.active_time())
                self.use_ultimate_defense(hp)
            self.previous_resources.clear()
            self.damage_until = float("-inf")
            self.party_damage_now = False
            self.party_damage_sources = ()
            self.party_hp_damage_rates.clear()
            self.summon_damage_rate = None
            self.summon_damage_until = float("-inf")
            self.strong_damage_until = float("-inf")
            self.reset_strong_aggressor_search()
            self.strong_aggressor_allowed = None
            self.support_damage_until = float("-inf")
            self.support_damage_rate = None
            self.update_support_recovery(None)
            return
        self.track_damage(image, hp)
        self.use_ultimate_defense(hp)
        self.use_mass_aggro()
        dead = target == 0 or (self.last_target is not None and self.last_target.dead is True)
        self.observation({"hp": hp, "mp": mp, "target_hp": target, "dead": dead,
                          "summon_hp": self.previous_resources.get("summon_hp"),
                          "summon_mp": self.previous_resources.get("summon_mp"),
                          "support_hp": self.previous_resources.get("support_hp"),
                          "support_mp": self.previous_resources.get("support_mp"),
                          "support_buff_active": self.buffing,
                          "buffs_pending": self.controller.active_time() >= self.next_buff
                          or any(getattr(self.config, f"buff{slot}_key") is not None
                                 and self.clock() >= self.buff_ready[slot] for slot in range(1, 6))})

    def _observe(self, resources_only: bool = False) -> tuple[float | None, float | None, float | None]:
        """
        Подтверждает опасность новым кадром до возврата управления бою и саппорту.
        """
        if self._ud_observing:
            return super()._observe(resources_only=resources_only)
        # Базовое чтение может повториться для HP/MP; подтверждение остаётся одним на вызов.
        self._ud_observing = True
        try:
            ready_before = self.ud_ready_at
            elixir_before = self.hp_elixir_ready
            mass_aggro_before = self.mass_aggro_ready_at
            result = super()._observe(resources_only=resources_only)
            if self.hp_elixir_ready != elixir_before:
                result = super()._observe(resources_only=resources_only)
            if self.ud_confirmations == 1 and not self.observation_recovering:
                self.report("УД МА: опасное HP/урон, подтверждение 1/2; сразу проверяем свежий кадр")
                self.controller.wait(.15)
                result = super()._observe(resources_only=resources_only)
            if self.ud_ready_at != ready_before:
                # После секундного удержания УД дальнейшие действия получают новый кадр.
                result = super()._observe(resources_only=resources_only)
            if self.mass_aggro_ready_at != mass_aggro_before:
                # После секундного каста цели и лечение получают новый кадр.
                result = super()._observe(resources_only=resources_only)
            return result
        finally:
            self._ud_observing = False

    def observe(self) -> tuple[float, float, float | None]:
        if self.support_lost:
            # В завершающем бою нет агра новых целей, хила и визитов к погибшему саппорту.
            return super().observe()
        previous = self.last_resources
        generation = self.controller.pause_generation
        resource_generation = self.resource_generation
        hp, mp, target = self.observe_ma()
        fresh_damage = (previous is not None and generation == resource_generation == self.controller.pause_generation
                        and (hp < previous[0] or self.own_cp is not None and previous[1] is not None
                             and self.own_cp < previous[1]))
        fresh_damage = fresh_damage or self.party_damage_now
        if self.summon_servicing:
            # Для призыва используем общий порог пати, а не любое дрожание полосы МА.
            fresh_damage = self.party_damage_now
        if ((fresh_damage or self.controller.active_time() < self.damage_until)
                and not self.summon_casting
                and (not self.casting_ma_buff or hp <= self.config.buff_min_hp)):
            # Не меняем погибшую/пропавшую цель во время подтверждения смерти и лута.
            if (not self.alive_target(target) and target != 0 and fresh_damage
                    and not self.attacking and not self.verifying_target):
                source = ", ".join(self.party_damage_sources) or "farmer_hp/cp"
                self.report(f"Падает HP/CP МА или пати без живой цели ({source}): "
                            "срочно выбираем агрессора через next target")
                self.invalidate_target()
                self.press(self.config.next_target)
                self.controller.wait(self.config.target_delay)
                hp, mp, target = self.observe_ma()
                self.attacking = False
            if self.alive_target(target) and not self.attacking:
                if self.reject_high_level_target():
                    target = None
                else:
                    self.report("Урон по МА/саппорту: агр и ответная атака приоритетнее баффов")
                    self.open_with_aggro()
                    if super().combat_key(self.config.attack, 100):
                        self.support_attack_started = True
                        self.attacking = True
        if self.update_support_buff():
            hp, mp, target = self.observe_ma()
        if ((self.mass_buff_active or self.controller.active_time() < self.mass_buff_ready)
                and self.critical_party()):
            self.visit(self.config.stop_attack_key, .1)
            self.mass_buff_active = False
            self.mass_buff_index = 0
            self.mass_buff_ready = float("-inf")
            self.next_buff = min(self.next_buff, self.controller.active_time())
            self.report("Критическое HP пати: серия масс-пати баффов прервана; после спасения повторим её сначала")
        if self.support_maintenance(hp, target):
            hp, mp, target = self.observe_ma()
            if self.update_support_buff():
                hp, mp, target = self.observe_ma()
        if self.buffing:
            if self.escaping:
                self.ensure_support_follow()
            return hp, mp, target
        support_hp = self.previous_resources.get("support_hp")
        fresh_support = (self.resource_generation == self.controller.pause_generation
                         and self.controller.active_time() - self.resources_at <= 1)
        if fresh_support and support_hp is not None:
            if support_hp >= self.config.support_self_heal_threshold:
                self.self_heals = 0
            elif (support_hp > 0 and self.config.support_self_heal_key is not None
                  and self.support_heal_key_ready(self.config.support_self_heal_key)
                  and self.config.support_self_heal_key != self.config.support_party_heal_key
                  and self.controller.active_time() - self.last_heal >= self.config.heal_cooldown):
                if self.self_heals >= self.config.heal_attempts:
                    raise RuntimeError("HP саппорта не восстановился после лимита попыток собственного хила")
                self.report(f"Собственный хил саппорта: HP {support_hp:.0%}")
                sent_at = self.visit(self.config.support_self_heal_key, self.config.heal_seconds,
                                     double_press=True)
                if sent_at is not None:
                    self.mark_support_heal(self.config.support_self_heal_key)
                    self.self_heals += 1
                # После собственного хила заново читаем состояние МА и его цели.
                hp, mp, target = self.observe_ma()
        if hp >= self.config.heal_threshold:
            self.heals = 0
        elif (self.config.heal_key is not None
              and self.config.heal_key != self.config.support_party_heal_key
              and self.support_heal_key_ready(self.config.heal_key)
              and self.controller.active_time() - self.last_heal >= self.config.heal_cooldown):
            if self.heals >= self.config.heal_attempts:
                raise RuntimeError("HP не восстановился после лимита попыток хила")
            self.report(f"Хил МА: HP {hp:.0%}")
            sent_at = self.visit(self.config.heal_key, self.config.heal_seconds, double_press=True)
            if sent_at is not None:
                self.mark_support_heal(self.config.heal_key)
                self.heals += 1
            # Действия по цели используют только новый кадр после возврата.
            hp, mp, target = self.observe_ma()
        if self.escaping:
            self.ensure_support_follow()
            return hp, mp, target
        if (not self.support_pending and not self.buffing and not self.verifying_target
                and self.attacking and self.alive_target(target)
                and self.controller.active_time() >= self.support_assist_retry_at):
            identity = self.monster_identity()
            same = (self.support_assist_identity is None
                    or identity is not None and not self.different_monsters(self.support_assist_identity, identity))
            if (same and self.support_assist_generation == self.controller.pause_generation
                    and 0 < self.support_assist_attempts <= self.config.support_assist_retries):
                self.report("Контрольный повтор ассиста саппорта по той же живой цели")
                self.support_pending = True
        if (self.support_pending and self.attacking and self.alive_target(target)
                and not self.support_in_danger()):
            self.assist_support()
            # Визит занимает реальное время: прежний HP цели больше не актуален.
            hp, mp, target = self.observe_ma()
        return hp, mp, target

    def observe_ma(self) -> tuple[float, float, float | None]:
        hp, mp, target = super().observe()
        # Проверяем расходники на каждом свежем кадре, в том числе после хила/ассиста.
        # Не более двух действий: эликсир и банка, каждый раз с новым измерением HP.
        for _ in range(2):
            if not self.support_consumables():
                break
            hp, mp, target = super().observe()
        self.track_support_attack(target)
        self.under_attack = self.under_attack or self.controller.active_time() < self.damage_until
        return hp, mp, target

    def reset_support_attack(self) -> None:
        self.support_attack_started = False
        self.support_attack_hp: float | None = None
        self.support_attack_confirmed = False
        self.support_attack_identity: tuple[str, int | None] | None = None
        self.support_attack_generation = self.controller.pause_generation

    def invalidate_target(self) -> None:
        self.strong_aggressor_allowed = None
        self.strong_aggressor_pending = False
        self.reset_support_attack()
        super().invalidate_target()

    def reset_strong_aggressor_search(self) -> None:
        self.strong_aggressor = None
        self.strong_aggressor_selections = 0

    def strong_aggressor_identity(self) -> tuple[str, int | None, int] | None:
        identity = self.monster_identity()
        maxima = self.target_hp_maxima()
        target = self.last_target
        if (identity is None or maxima is None or target is None
                or target.hp is None or target.hp <= 0 or target.dead or target.death_marker
                or target.player_class or target.clan):
            return None
        return *identity, maxima[1]

    def target_hp_too_large(self) -> bool:
        if (self.strong_aggressor_generation != self.controller.pause_generation
                or self.strong_aggressor_identity() != self.strong_aggressor_allowed):
            self.strong_aggressor_allowed = None
        return super().target_hp_too_large() and self.strong_aggressor_allowed is None

    def press(self, key: str) -> None:
        if key == self.config.next_target:
            self.strong_aggressor_allowed = None
            self.strong_aggressor_pending = False
            if self.strong_aggressor_generation != self.controller.pause_generation:
                self.reset_strong_aggressor_search()
            self.strong_aggressor_generation = self.controller.pause_generation
        super().press(key)
        if key == self.config.next_target:
            self.strong_aggressor_pending = True

    def reject_unsafe_target(self) -> bool:
        # Одна попытка — одна отправленная команда next target, не число проверок CV.
        if self.strong_aggressor_pending:
            self.strong_aggressor_pending = False
            identity = self.strong_aggressor_identity()
            now = self.controller.active_time()
            if (identity is None or not super().target_hp_too_large()
                    or self.strong_aggressor_generation != self.controller.pause_generation
                    or self.resource_generation != self.controller.pause_generation
                    or not 0 <= now - self.resources_at <= 1
                    or now >= self.strong_damage_until or self.observation_recovering
                    or self.support_lost):
                self.reset_strong_aggressor_search()
            else:
                if identity != self.strong_aggressor:
                    self.reset_strong_aggressor_search()
                    self.strong_aggressor = identity
                self.strong_aggressor_selections += 1
                self.report(f"Поиск сильного агрессора «{identity[0]}»: "
                            f"{self.strong_aggressor_selections}/{STRONG_AGGRESSOR_SELECTIONS}; "
                            "подтверждён сильный урон по пати")
                if self.strong_aggressor_selections >= STRONG_AGGRESSOR_SELECTIONS:
                    self.strong_aggressor_allowed = identity
                    self.unsafe_target_rejected = False
                    self.report(f"Ответная атака по «{identity[0]}»: HP-ограничение снято "
                                "до завершения боя или смены цели после 6 выборов агрессора")
                    self.reset_strong_aggressor_search()
        return super().reject_unsafe_target()

    def track_support_attack(self, target: float | None) -> None:
        identity = self.monster_identity()
        if (self.support_attack_generation != self.controller.pause_generation
                or self.different_monsters(self.support_attack_identity, identity)):
            self.reset_support_attack()
        if not self.alive_target(target):
            self.support_attack_hp = None
            self.support_attack_confirmed = False
            return
        if self.casting_ma_buff:
            if not self.support_attack_started:
                self.support_attack_hp = target
        elif not self.attacking or not self.support_attack_started:
            self.support_attack_started = False
            self.support_attack_confirmed = False
            self.support_attack_hp = target
        elif self.support_attack_hp is None:
            self.support_attack_hp = target
        elif (self.support_attack_started
              and self.support_attack_hp - target >= self.config.progress_epsilon):
            if not self.support_attack_confirmed:
                self.report("После атаки МА подтверждено снижение HP цели: ассист саппорта разрешён")
            self.support_attack_confirmed = True
        if identity is not None:
            self.support_attack_identity = identity

    def support_heal_key_ready(self, key: str) -> bool:
        now = self.controller.active_time()
        return (now >= self.support_heal_ready.get(key, float("-inf"))
                and (key != self.config.support_party_heal_key or now >= self.support_party_heal_ready))

    def mark_support_heal(self, key: str, *, at: float | None = None) -> None:
        # Хил может сменить цель саппорта и остановить его атаку.
        self.support_pending = True
        self.following = False
        self.last_heal = self.controller.active_time() if at is None else at
        config = self.config
        cooldowns = [cooldown for assigned, cooldown in (
            (config.heal_key, config.heal_cooldown),
            (config.support_self_heal_key, config.heal_cooldown),
            (config.support_party_heal_key, config.support_party_heal_cooldown),
            (config.support_vampiric_key, config.support_vampiric_cooldown),
        ) if assigned == key]
        # Одна физическая кнопка не получает отдельные откаты через разные ветки хила.
        self.support_heal_ready[key] = max(self.support_heal_ready.get(key, float("-inf")),
                                           self.last_heal + max(cooldowns, default=0.))

    def update_support_recovery(self, hp: float | None) -> None:
        if (self.support_vampiric_recovering and not self.support_lost
                and self.support_vampiric_generation == self.controller.pause_generation
                and self.config.support_vampiric_key is not None and hp is not None
                and hp >= self.config.support_vampiric_hp_threshold):
            # Восстановление HP явно возвращает ассист в очередь с новым лимитом повторов.
            self.support_pending = True
            self.support_assist_attempts = 0
            self.support_assist_retry_at = float("inf")
            self.support_rescue_until = float("-inf")
            self.report(f"HP саппорта восстановлено до {hp:.1%}: "
                        "возвращаем ассист по живой цели МА в очередь")
        if (self.support_vampiric_generation != self.controller.pause_generation
                or self.config.support_vampiric_key is None or hp is None or hp <= 0
                or hp >= self.config.support_vampiric_hp_threshold):
            self.support_vampiric_recovering = False
            self.support_vampiric_attempts = 0
            self.support_vampiric_verify = False
        elif self.support_vampiric_verify:
            self.report(f"После попытки вампирика саппорта {self.support_vampiric_attempts}: "
                        f"HP {hp:.1%}, порог {self.config.support_vampiric_hp_threshold:.0%} "
                        "не достигнут; продолжим лечение после отката")
            self.support_vampiric_verify = False

    def support_in_danger(self) -> bool:
        hp = self.previous_resources.get("support_hp")
        now = self.controller.active_time()
        return (self.resource_generation == self.controller.pause_generation
                and 0 <= now - self.resources_at <= 1 and hp is not None
                and 0 < hp < max(.6, self.config.support_vampiric_hp_threshold)
                and (now < self.support_damage_until
                     or self.support_vampiric_recovering
                     and self.support_vampiric_generation == self.controller.pause_generation))

    def support_consumables(self) -> bool:
        if self.support_lost:
            return False
        now = self.controller.active_time()
        support_hp = self.previous_resources.get("support_hp")
        if (self.resource_generation != self.controller.pause_generation
                or not 0 <= now - self.resources_at <= 1):
            return False
        config = self.config
        heavy_damage = now < self.support_damage_until
        if (support_hp is not None and 0 < support_hp < config.support_hp_elixir_hp_threshold
                and config.support_hp_elixir_key is not None
                and self.hp_elixir_clock() >= self.support_hp_elixir_ready):
            self.report(f"Сильный HP-эликсир саппорта: HP {support_hp:.1%}, "
                        f"кнопка {config.support_hp_elixir_key}; откат {config.support_hp_elixir_cooldown:g} с")
            self.visit(config.support_hp_elixir_key, .1, settle_seconds=.15,
                       key_milliseconds=150, repeat=heavy_damage)
            self.support_hp_elixir_ready = self.hp_elixir_clock() + config.support_hp_elixir_cooldown
            return True
        if (support_hp is not None and 0 < support_hp < config.support_potion_hp_threshold
                and config.support_potion_key is not None and now >= self.support_potion_ready):
            self.report(f"Зелье саппорта: HP {support_hp:.0%}")
            self.visit(config.support_potion_key, .1, settle_seconds=.15, key_milliseconds=150)
            self.support_potion_ready = self.controller.active_time() + config.support_potion_cooldown
            return True
        return False

    def use_support_preserv(self, hp: float) -> bool:
        """
        Макрос Preserv выбирает МА; барьер также дублируется на его саммона.
        """
        config = self.config
        now = self.controller.active_time()
        if (config.support_preserv_key is None
                or self.support_lost or self.buffing or hp <= 0
                or self.party_profile is None
                or (self.previous_resources.get("support_hp") or 0) <= 0
                or self.resource_generation != self.controller.pause_generation
                or not 0 <= now - self.resources_at <= 1
                or now < self.support_preserv_ready):
            return False
        heavy = (self.ud_damage_rate is not None
                 and self.ud_damage_rate > config.support_preserv_damage_per_second)
        summon_hp = self.previous_resources.get("summon_hp")
        summon_heavy = (summon_hp is not None and summon_hp > 0
                        and now < self.summon_damage_until
                        and self.summon_damage_rate is not None
                        and self.summon_damage_rate > config.support_preserv_damage_per_second + 1e-9)
        wounded = [(name, value) for name, value in (("МА", hp), ("саммон", summon_hp))
                   if value is not None and 0 < value < config.support_preserv_hp_threshold]
        if not wounded and not heavy and not summon_heavy:
            return False
        reasons = [f"{name} HP {value:.1%}" for name, value in wounded]
        if heavy:
            reasons.append(f"сильный урон по МА {self.ud_damage_rate:.1%}/с")
        if summon_heavy:
            reasons.append(f"сильный урон по пантере {self.summon_damage_rate:.1%}/с, HP {summon_hp:.1%}")
        generation = self.controller.pause_generation
        self.check()
        self.controller.switch_window(self.support)
        self.observe_support_life()
        self.following = False
        if generation != self.controller.pause_generation:
            self.controller.switch_window(self.farmer)
            return False
        sent_at = self.send_support_skill(config.support_preserv_key, generation)
        if sent_at is None:
            self.controller.switch_window(self.farmer)
            return False
        self.support_preserv_ready = sent_at + config.support_preserv_cooldown
        self.report("Preserv саппорта на МА и саммона: " + "; ".join(reasons)
                    + f"; кнопка {config.support_preserv_key}, каст {config.support_preserv_cast_seconds:g} с; "
                    "длительность баффа 10 с; "
                    "отправка не подтверждает получение барьера")
        self.controller.wait(max(0., sent_at + config.support_preserv_cast_seconds
                                  - self.controller.active_time()))
        self.check()
        self.controller.switch_window(self.farmer)
        self.support_pending = self.attacking
        return True

    def lifeimpact_available(self, hp: float | None) -> bool:
        """
        Проверяет свежее HP МА и расчётный ChantOfLife, отдельно от регена всей пати.
        """
        config = self.config
        now = self.controller.active_time()
        remaining = self.support_ma_chant_until - now
        chant_started = self.support_ma_chant_until - PARTY_REGEN_SECONDS
        return (config.support_lifeimpact_key is not None and not self.support_lost
                and not self.buffing and hp is not None and 0 < hp < config.support_lifeimpact_hp_threshold
                and self.party_profile is not None
                and (self.previous_resources.get("support_hp") or 0) > 0
                and self.resource_generation == self.support_ma_chant_generation == self.controller.pause_generation
                and 0 <= now - self.resources_at <= 1
                and now >= self.support_lifeimpact_ready
                and now >= chant_started and remaining > 0
                and (hp < config.support_lifeimpact_emergency_hp
                     or remaining < config.support_lifeimpact_regen_remaining))

    def use_support_lifeimpact(self, hp: float) -> bool:
        """
        Макрос выбирает только МА, применяет LifeImpact и возобновляет следование.
        """
        if not self.lifeimpact_available(hp):
            return False
        config = self.config
        generation = self.controller.pause_generation
        self.check()
        self.controller.switch_window(self.support)
        self.observe_support_life()
        # Переключение окна и проверка жизни могут пережить бафф или паузу.
        self.check()
        if not self.lifeimpact_available(hp):
            self.controller.switch_window(self.farmer)
            return False
        remaining = self.support_ma_chant_until - self.controller.active_time()
        sent_at = self.send_support_skill(config.support_lifeimpact_key, generation)
        if sent_at is None:
            self.controller.switch_window(self.farmer)
            return False
        self.support_lifeimpact_ready = sent_at + config.support_lifeimpact_cooldown
        # Успех не подтверждён: консервативно считаем ChantOfLife на МА израсходованным.
        self.support_ma_chant_until = float("-inf")
        self.report(f"LifeImpact на МА: HP {hp:.1%}, расчётный остаток ChantOfLife {remaining:.1f} с; "
                    f"кнопка {config.support_lifeimpact_key}, два нажатия с паузой 85 мс; "
                    "+15% max HP при успехе, применение не подтверждено")
        self.check()
        self.controller.switch_window(self.farmer)
        self.wait_with_emergency_checks(max(0., sent_at + config.support_lifeimpact_cast_seconds
                                             - self.controller.active_time()))
        self.following = generation == self.controller.pause_generation
        self.follow_generation = generation
        self.support_pending = self.attacking
        return True

    def support_maintenance(self, hp: float, target: float | None) -> bool:
        if self.support_lost:
            return False
        now = self.controller.active_time()
        if (self.resource_generation != self.controller.pause_generation
                or not 0 <= now - self.resources_at <= 1):
            return False
        support_hp = self.previous_resources.get("support_hp")
        config = self.config
        if self.support_consumables():
            return True
        if ((support_hp is None or 0 < support_hp < max(.6, config.support_vampiric_hp_threshold))
                and now - self.support_status_at >= 2):
            rate = f"{self.support_damage_rate:.1%}/с" if now < self.support_damage_until else "не подтверждён"
            elixir = ("отключён" if config.support_hp_elixir_key is None else
                      f"{config.support_hp_elixir_key}, HP <{config.support_hp_elixir_hp_threshold:.0%}, "
                      f"откат {max(0., self.support_hp_elixir_ready - self.hp_elixir_clock()):.1f} с")
            potion = ("отключена" if config.support_potion_key is None else
                      f"{config.support_potion_key}, HP <{config.support_potion_hp_threshold:.0%}, "
                      f"откат {max(0., self.support_potion_ready - now):.1f} с")
            hp_text = f"{support_hp:.1%}" if support_hp is not None else "неизвестно, лечение заблокировано"
            self.report(f"Спасение саппорта: HP {hp_text}, сильный урон {rate}; "
                        f"эликсир: {elixir}; банка: {potion}; "
                        f"вампирик: {config.support_vampiric_key or 'отключён'}, "
                        f"откат {max(0., self.support_vampiric_ready - now):.1f} с, "
                        f"HP цели {target}; бафф {self.buffing}")
            self.support_status_at = now
        # Зелье не прерывает бафф; остальные скиллы ждут его завершения или отмены.
        if self.buffing:
            return False
        if self.use_support_preserv(hp):
            return True
        # Готовый точечный хил не должен ждать нового F9, обновляющего таймер ChantOfLife.
        if self.use_support_lifeimpact(hp):
            return True
        wounded = [(name, value) for name, value in (
            ("МА", hp), ("саппорт", support_hp), ("самон", self.previous_resources.get("summon_hp")))
            if value is not None and 0 < value < config.support_party_heal_threshold]
        if (config.support_party_heal_key is not None and now >= self.support_party_heal_ready
                and self.support_heal_key_ready(config.support_party_heal_key)
                and wounded):
            reason = ", ".join(f"{name} HP {value:.1%}" for name, value in wounded)
            self.report(f"Пати-хил саппорта: {reason}")
            new_series = now >= self.support_party_regen_until
            generation = self.controller.pause_generation
            sent_at = self.visit(config.support_party_heal_key, .1, double_press=True)
            if sent_at is None:
                return False
            cast_end = sent_at + config.support_party_heal_cast_seconds
            self.mark_support_heal(config.support_party_heal_key, at=cast_end)
            self.support_party_heal_attempts = 1 if new_series else self.support_party_heal_attempts + 1
            self.support_party_regen_until = cast_end + PARTY_REGEN_SECONDS
            self.support_ma_chant_until = self.support_party_regen_until
            self.support_ma_chant_generation = generation
            self.support_party_heal_ready = max(cast_end + config.support_party_heal_cooldown,
                                                self.support_heal_ready[config.support_party_heal_key])
            if self.support_party_heal_attempts >= 1 + PARTY_REGEN_RETRIES:
                # После начального каста и трёх повторов ждём расчётный эффект последнего.
                self.support_party_heal_ready = max(self.support_party_heal_ready,
                                                    self.support_party_regen_until)
            self.report(f"Пати-реген {config.support_party_heal_key} отправлен: "
                        f"попытка {self.support_party_heal_attempts}/{1 + PARTY_REGEN_RETRIES}, "
                        "двойное нажатие с паузой 85 мс; расчётный эффект 15 с, применение каста не подтверждено")
            self.wait_with_emergency_checks(max(0., cast_end - self.controller.active_time()))
            return True
        if wounded and now - self.party_heal_status_at >= 2:
            reason = ("отключён" if config.support_party_heal_key is None else
                      f"расчётный бафф ещё {max(0., self.support_party_regen_until - now):.1f} с; "
                      f"попыток {self.support_party_heal_attempts}/{1 + PARTY_REGEN_RETRIES}; "
                      f"повтор через {max(0., self.support_party_heal_ready - now):.1f} с")
            self.report(f"Пати-реген: {reason}; " + ", ".join(f"{name} HP {value:.1%}" for name, value in wounded))
            self.party_heal_status_at = now
        if (not self.buffing and config.support_vampiric_key is not None
                and support_hp is not None and 0 < support_hp < config.support_vampiric_hp_threshold
                and self.alive_target(target)
                and now >= self.support_vampiric_ready):
            self.report(f"Вампирик саппорта: своё HP {support_hp:.0%}, HP монстра {target:.1%}; "
                        f"попытка лечения {self.support_vampiric_attempts + 1}")
            generation = self.controller.pause_generation
            if not self.visit_vampiric(repeat=now < self.support_damage_until):
                return False
            self.mark_support_heal(config.support_vampiric_key)
            self.support_vampiric_ready = self.controller.active_time() + config.support_vampiric_cooldown
            # Кнопка и ожидание каста не доказывают лечение: низкое HP сохраняет приоритет спасения.
            self.support_vampiric_generation = generation
            self.support_vampiric_recovering = generation == self.controller.pause_generation
            self.support_vampiric_verify = self.support_vampiric_recovering
            self.support_vampiric_attempts += 1
            return True
        return False

    def visit_vampiric(self, *, repeat: bool = False) -> bool:
        if self.support_lost or not self.validate_target_before_attack():
            return False
        generation = self.controller.pause_generation
        self.check()
        self.controller.switch_window(self.support)
        self.observe_support_life()
        self.controller.wait(.15)
        self.check()
        if generation != self.controller.pause_generation:
            self.controller.switch_window(self.farmer)
            return False
        # Игровой макрос ассиста выбирает монстра МА перед вампириком.
        self.controller.key(self.config.support_key, 150, exact=True)
        self.controller.wait(self.config.support_seconds)
        sent_at = self.send_support_skill(self.config.support_vampiric_key, generation)
        self.check()
        self.controller.switch_window(self.farmer)
        if sent_at is None:
            return False
        self.wait_with_emergency_checks(self.config.support_vampiric_cast_seconds)
        if repeat:
            # Следующий свежий цикл лечения важнее отдельного визита для обычного ассиста.
            self.support_rescue_until = self.controller.active_time() + self.config.poll_seconds
            self.support_rescue_generation = generation
        self.following = False
        return True

    def protect(self) -> tuple[float | None, bool]:
        _, _, target = self.observe()
        dead = target == 0 or (self.last_target is not None and self.last_target.dead is True)
        return target, dead

    def saving_mana_for_summon(self) -> bool:
        """
        Сохраняет MP МА до подтверждения пантеры, разрешая только автоатаку.
        """
        return (self.config.summon_key is not None and not self.support_lost
                and (not self.summon_recovery.checked or self.summon_recovery.pending))

    def saving_mana_for_buffs(self) -> bool:
        return self.saving_mana_for_summon() or super().saving_mana_for_buffs()

    def open_with_aggro(self) -> None:
        # Спасательный агр тоже расходует ману, необходимую для призыва.
        if not self.saving_mana_for_summon():
            super().open_with_aggro()

    def combat_key(self, key: str, duration: int) -> bool:
        if (self.saving_mana_for_summon() and key != self.config.attack
                and key in {self.config.aggro_key, self.config.vampiric_key, self.config.ud_key,
                            *self.config.damage_keys, *self.config.overhit_keys}):
            return False
        if self.support_attack_generation != self.controller.pause_generation:
            self.reset_support_attack()
        if not super().combat_key(key, duration):
            return False
        if (not self.casting_ma_buff
                and key in {self.config.attack, *self.config.damage_keys, *self.config.overhit_keys}):
            self.support_attack_started = True
        if (self.support_pending and not self.buffing
                and key in {self.config.attack, *self.config.damage_keys, *self.config.overhit_keys}):
            # Свежий HP МА и приоритетный хил проверяются до ассиста.
            _, _, target = self.observe()
            if self.support_pending and self.alive_target(target):
                self.assist_support()
        return True

    def assist_support(self) -> None:
        if (self.target_validating or self.support_lost or self.escaping or self.buffing or self.verifying_target
                or self.controller.active_time() < self.mass_buff_ready
                or self.casting_ma_buff or not self.attacking or not self.support_attack_started
                or (not self.config.experimental_fast_farm and not self.support_attack_confirmed)
                or self.support_in_danger()
                or self.critical_party()
                or (self.support_rescue_generation == self.controller.pause_generation
                    and self.controller.active_time() < self.support_rescue_until)
                or self.reject_unsafe_target() or not self.validate_target_before_attack()):
            return
        self.report("Живая цель МА: запускаем ассист саппорта")
        # После смены окна игра должна принять фокус до нажатия макроса.
        self.visit(self.config.support_key, self.config.support_seconds,
                   settle_seconds=.5, key_milliseconds=150)
        self.support_pending = False
        self.support_assist_attempts += 1
        self.support_assist_identity = self.monster_identity()
        self.support_assist_generation = self.controller.pause_generation
        self.support_assist_retry_at = (self.controller.active_time() + self.config.support_assist_retry_seconds
                                        if self.support_assist_attempts <= self.config.support_assist_retries
                                        else float("inf"))
        self.report(f"Команда ассиста саппорта {self.config.support_key} отправлена: "
                    f"попытка {self.support_assist_attempts}")
        self.following = False

    def on_target_missing(self) -> None:
        if self.support_lost:
            raise SupportDied("Саппорт погиб: текущая цель отсутствует, фарм завершён")
        if self.buffing:
            return
        self.ensure_support_follow()

    def ensure_support_follow(self) -> None:
        if not self.escaping and self.controller.active_time() < self.mass_buff_ready:
            return
        if not self.following or self.follow_generation != self.controller.pause_generation:
            generation = self.controller.pause_generation
            reason = "Выход МА из застревания" if self.escaping else "Next target не выбрал живую цель"
            self.report(f"{reason}: включаем следование саппорта")
            self.visit(self.config.follow_key, self.config.support_seconds)
            self.following = generation == self.controller.pause_generation
            self.follow_generation = generation

    def _fight(self, target: float | None = None) -> None:
        previous = self.support_pending
        self.reset_support_attack()
        self.support_attack_hp = target
        self.support_attack_identity = self.monster_identity()
        self.support_attack_started = self.fast_target_ready
        self.support_assist_attempts = 0
        self.support_assist_retry_at = float("inf")
        if not self.fast_target_ready:
            self.support_pending = True
        try:
            super()._fight(target)
        finally:
            self.support_assist_retry_at = float("inf")
            self.support_pending = previous or self.fast_target_ready
            self.attacking = self.fast_target_ready
            self.reset_support_attack()
            self.support_attack_started = self.fast_target_ready

    def switch_fast_target(self) -> None:
        super().switch_fast_target()
        self.support_attack_started = self.fast_target_ready

    def allow_fast_target_switch(self) -> bool:
        return (super().allow_fast_target_switch() and not self.summon_servicing
                and not self.summon_recovery.pending and not self.summon_recovery.rebuff)

    def cancel_summon_cast(self) -> None:
        self.controller.key(self.config.stop_attack_key, 80, exact=True)
        self.summon_casting = False
        self.report("Призыв пантеры прерван: урон, опасное HP или потеря наблюдения; "
                    "сначала защищаем пати, затем повторяем проверку безопасности")

    def summon_mana_ready(self) -> bool:
        """
        Требует свежего MP МА не ниже стоимости одного призыва пантеры.
        """
        return (self.last_mp is not None and self.last_mp >= SUMMON_MIN_MP
                and self.resource_generation == self.controller.pause_generation
                and 0 <= self.clock() - self.resources_at <= 1)

    def cast_summon(self) -> None:
        state = self.summon_recovery
        if self.config.summon_key is None or not state.pending:
            return
        if state.attempts >= SUMMON_MAX_ATTEMPTS:
            raise RuntimeError(f"Пантера не подтверждена после {SUMMON_MAX_ATTEMPTS} попыток призыва; "
                               "фарм остановлен")
        if self.sitting:
            self.press(self.config.stand)
            self.sitting = False
            state.quiet_since = None
            return
        self.check()
        generation = self.controller.pause_generation
        started = self.clock()
        if (not state.quiet(started, self.config.quiet_seconds, generation)
                or not self.summon_mana_ready()):
            return
        self.controller.key(self.config.summon_key, 100, exact=True)
        state.attempts += 1
        state.ready_at = started + self.config.summon_cooldown
        until = started + self.config.summon_cast_seconds
        self.summon_casting = True
        self.report(f"Призыв пантеры: попытка {state.attempts}/{SUMMON_MAX_ATTEMPTS}, {self.config.summon_key}; "
                    f"каст {self.config.summon_cast_seconds:g} с, "
                    f"откат {self.config.summon_cooldown:g} с от начала")
        try:
            while self.summon_casting:
                self.check()
                if generation != self.controller.pause_generation:
                    self.cancel_summon_cast()
                    state.invalidate()
                    return
                self.observe()
                if not self.summon_casting:
                    return
                if (generation != self.controller.pause_generation or state.quiet_since is None
                        or state.quiet_since > started):
                    self.cancel_summon_cast()
                    return
                remaining = until - self.clock()
                if remaining <= 0:
                    hp = self.previous_resources.get("summon_hp")
                    if hp is None or hp <= 0:
                        mp = self.previous_resources.get("summon_mp")
                        readings = ", ".join(f"{name} {value:.1%}" if value is not None
                                             else f"{name} неизвестно"
                                             for name, value in (("HP", hp), ("MP", mp)))
                        self.report(f"Пантера не подтверждена после каста: попытка "
                                    f"{state.attempts}/{SUMMON_MAX_ATTEMPTS}; {readings}")
                        if state.attempts >= SUMMON_MAX_ATTEMPTS:
                            raise RuntimeError(f"Пантера не подтверждена после "
                                               f"{SUMMON_MAX_ATTEMPTS} попыток призыва; фарм остановлен")
                    return
                self.controller.wait(min(self.config.poll_seconds, remaining))
        finally:
            self.summon_casting = False

    def service_summon(self) -> None:
        """
        Ожидает призыва; при нехватке MP переносит пати-баффы в обычный фарм.
        """
        state = self.summon_recovery
        if self.config.summon_key is None or self.summon_servicing:
            return
        if state.checked and not state.pending and not state.rebuff:
            return
        self.summon_servicing = True
        progress_at = self.clock()
        progress = (state.attempts, state.pending, state.rebuff, self.buff_actions)
        report_at = self.clock() + 10
        self.report(f"Проверка пантеры: перед призывом ждём {self.config.quiet_seconds:g} с "
                    "свежего наблюдения без урона по пати")
        try:
            while not state.checked or state.pending or state.rebuff:
                self.check()
                _, _, target = self.observe()
                current = (state.attempts, state.pending, state.rebuff, self.buff_actions)
                if current != progress:
                    progress_at = self.clock()
                    progress = current
                reason = state.wait_reason(self.previous_resources, self.clock(),
                                           self.config.quiet_seconds, self.controller.pause_generation)
                if state.pending and not self.summon_mana_ready():
                    mp = "неизвестно" if self.last_mp is None else f"{self.last_mp:.1%}"
                    reason += f"; MP МА {mp}, для призыва требуется ≥10%; защита только автоатакой"
                    if (self.last_mp is not None and self.last_mp < SUMMON_MIN_MP
                            and self.resource_generation == self.controller.pause_generation
                            and 0 <= self.clock() - self.resources_at <= 1
                            and all(self.previous_resources.get(name) is not None
                                    for name in state.required_resources)):
                        # Известное низкое MP разрешает ждать регена дольше watchdog.
                        # Пропуски обязательных наблюдений по-прежнему ограничены по времени.
                        progress_at = self.clock()
                if self.critical_party():
                    reason += f"; опасное HP пати: {self.critical_party()}"
                if state.rebuff:
                    reason += (f"; ребафф, MP саппорта: {self.previous_resources.get('support_mp')}, "
                               f"порог {self.config.support_buff_min_mp:g}")
                # Штатный длинный макрос и cooldown не считаем зависанием.
                timeout = max(SUMMON_STALL_SECONDS,
                              self.config.summon_cooldown + self.config.quiet_seconds + 1)
                if not self.buffing and self.clock() - progress_at >= timeout:
                    raise RuntimeError(f"Восстановление пантеры без прогресса {timeout:g} с: {reason}; "
                                       f"попыток призыва {state.attempts}/{SUMMON_MAX_ATTEMPTS}; фарм остановлен")
                if self.clock() >= report_at:
                    self.report(f"Ожидание восстановления пантеры: {reason}; "
                                f"без прогресса {self.clock() - progress_at:.0f}/{timeout:g} с; "
                                f"попыток призыва {state.attempts}/{SUMMON_MAX_ATTEMPTS}")
                    report_at = self.clock() + 10
                if self.attacking and self.alive_target(target):
                    self.summon_defending = True
                    try:
                        self.fight(target)
                    finally:
                        self.summon_defending = False
                    state.quiet_since = None
                    continue
                # Пропавшее HP цели не разрешает сохранять неизвестную автоатаку при призыве.
                if self.attacking or self.fast_target_ready:
                    self.controller.key(self.config.stop_attack_key, 80, exact=True)
                    self.attacking = self.fast_target_ready = False
                    self.reset_support_attack()
                    state.quiet_since = None
                if state.pending and (self.previous_resources.get("summon_hp") or 0) > 0:
                    if self.buffing:
                        # Макрос, начатый до появления пантеры, не считается её ребаффом.
                        self.visit(self.config.stop_attack_key, .1)
                        self.buffing = False
                        self.support_buff_until = None
                        self.support_buff_stage = 0
                        state.quiet_since = None
                    state.pending = False
                    state.rebuff = True
                    state.attempts = 0
                    # Баффы, отправленные до появления пантеры, ей не засчитываются.
                    self.mass_buff_index = 0
                    self.mass_buff_active = False
                    self.post_summon_mass_buff = False
                    self.mass_buff_ready = float("-inf")
                    self.next_buff = float("-inf")
                    for slot in self.buff_ready:
                        self.buff_ready[slot] = float("-inf")
                    self.report("Пантера подтверждена по свежему HP: полный ребафф саппорта и МА")
                if state.rebuff and not state.pending and not self.buffing:
                    mp = self.previous_resources.get("support_mp")
                    keys = [getattr(self.config, f"support_mass_buff{slot}_key")
                            for slot in (3, 4, 5, 6, 7, 8, 9, 0, "_divide", "_multiply")]
                    if (self.config.support_mass_buff_enabled and any(keys)
                            and (mp is None or mp < self.config.support_buff_min_mp)):
                        # Очередь баффов обслуживается обычным фармом без ожидания регена MP.
                        self.post_summon_mass_buff = True
                        state.rebuff = False
                        state.attempts = 0
                        self.report("MP саппорта недостаточно для полного ребаффа: продолжаем фарм, "
                                    "пати-баффы накладываем по очереди по мере появления MP")
                        return
                if state.checked and not state.pending and not state.rebuff:
                    return
                if (not self.buffing and not self.critical_party()
                        and 0 <= self.clock() - state.observed_at <= 1
                        and all(self.previous_resources.get(name) is not None
                                for name in state.required_resources)):
                    # Ожидание самона не отключает штатный watchdog неудачных боёв.
                    self.summon_defending = True
                    try:
                        escaped = self.escape_if_no_kills()
                    finally:
                        self.summon_defending = False
                    if escaped:
                        state.quiet_since = None
                        continue
                if state.quiet(self.clock(), self.config.quiet_seconds, self.controller.pause_generation):
                    if (state.pending and self.clock() >= state.ready_at
                            and not self.critical_party() and self.summon_mana_ready()):
                        self.cast_summon()
                        continue
                    if state.rebuff and not state.pending:
                        self.cast_buffs()
                        if (not state.pending and (self.previous_resources.get("summon_hp") or 0) > 0
                                and not self.buffing and not self.buffs_due()):
                            state.rebuff = False
                            state.attempts = 0
                            self.report("Ребафф после призыва завершён: поиск и атака мобов разрешены")
                            return
                self.controller.wait(self.config.poll_seconds)
        finally:
            self.summon_servicing = False

    def run(self) -> None:
        try:
            if self.config.summon_key is not None:
                if not isinstance(self.profile, BarProfile) or self.profile.summon is None:
                    raise RuntimeError("Для автопризыва настройте панель самона в окне МА "
                                       "или отключите summon_key")
                if self.party_profile is None:
                    raise RuntimeError("Для безопасного автопризыва настройте наблюдение пати МА")
            self.controller.switch_window(self.farmer)
            self.observe()
            cycle = 0
            while cycle < self.config.cycles:
                try:
                    self.service_summon()
                    self.run_cycle(cycle)
                    cycle += 1
                except Rebuffed:
                    self.report("Бафф завершён; заново ищем живую цель")
        except SupportDied:
            self.finish_current_without_support()
            raise SupportDied("Саппорт погиб: текущий бой завершён, новые цели запрещены") from None
        self.report("Дуо-фарм МА + саппорт завершён")

    def finish_current_without_support(self) -> None:
        """
        Завершает только уже выделенную цель с прежними ограничениями боя.
        """
        identity = self.monster_identity()
        self.report("Саппорт погиб: новых целей не берём; проверяем текущую цель МА")
        self.support_pending = False
        self.fast_target_ready = False
        # Смена цели быстрого фарма при малом HP запрещена до конца запуска.
        self.config = self.config.model_copy(update={"experimental_fast_farm": False})
        _, _, target = self.observe()
        if (not self.alive_target(target) or self.reject_unsafe_target()
                or self.different_monsters(identity, self.monster_identity())):
            return
        self.fight(target)

    def before_target_selection(self) -> None:
        if self.support_lost:
            raise SupportDied("Саппорт погиб: выбор новых целей запрещён")
        if not self.summon_defending:
            self.service_summon()
        super().before_target_selection()

    def cancel_target_run(self) -> None:
        if self.support_lost:
            raise SupportDied("Саппорт погиб: текущая цель недоступна, фарм завершён без движения")
        super().cancel_target_run()

    def escape_from_unreachable(self, name: str | None, *,
                                blocked_names: set[str] | None = None) -> float | None:
        if self.support_lost:
            raise SupportDied("Саппорт погиб: текущая цель недоступна, фарм завершён")
        self.escaping = True
        try:
            return super().escape_from_unreachable(name, blocked_names=blocked_names)
        finally:
            self.escaping = False

    def on_own_death(self) -> None:
        if self.support_lost:
            death = getattr(self.notify, "death", None)
            if death is not None:
                death()
            raise SupportDied("МА тоже погиб: фарм завершён, режим воскрешения ещё не настроен")
        super().on_own_death()
