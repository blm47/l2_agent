"""
Детерминированная политика хилера: ресурсы, динамика урона и проверка эффекта.
"""

import logging
import random
from collections import deque
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hid_pilot.party import PartyMember
from hid_pilot.script import KEYS

Skill = Literal["balance", "battle", "major", "mass_major", "group", "avatar", "invocation", "recharge", "purify", "mp_buff", "mp_buff2", "self_rescue", "area_heal"]
logger = logging.getLogger("hid_pilot.healer")


class HealerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    follow_slot: int = Field(default=1, ge=1, le=8)
    follow_seconds: float = Field(default=4, ge=1, le=300)
    follow_key: str | None = "9"
    follow_select_mouse: bool = False
    chain_heals: bool = True
    cancel_unneeded_casts: bool = False
    heal_press_interval: float = Field(default=.15, ge=.1, le=1)
    humanize: bool = True
    auto_loot: bool = True
    loot_key: str = "C"
    cursor_jitter: float = Field(default=.15, ge=0, le=.15)
    party_click_jitter: float = Field(default=.001, ge=0, le=.001)
    mp_buff_key: str | None = "F11"
    mp_buff_cooldown: float = Field(default=900, ge=900, le=3600)
    mp_buff_cast: float = Field(default=1, ge=1, le=5)
    mp_buff2_key: str | None = "F10"
    mp_buff2_cooldown: float = Field(default=900, ge=1, le=3600)
    mp_buff2_cast: float = Field(default=1, ge=0, le=5)
    invocation_mp: float = Field(default=.8, gt=0, le=1)
    support_start_delay: float = Field(default=30, ge=30, le=300)
    maintain_hp: float = Field(default=.7, gt=.5, lt=1)
    missing_hp_timeout: float = Field(default=45, gt=15, le=300)
    stun_probe_key: str = "SPACE"
    stun_probe_hold_ms: int = Field(default=500, ge=100, le=1000)
    stun_probe_timeout: float = Field(default=30, ge=7, le=30)
    stun_probe_effect: float = Field(default=.005, gt=0, le=.05)
    single_hp: float = Field(default=.5, gt=0, lt=1)
    single_damage: float = Field(default=.1, gt=0, le=1)
    balance_gap: float = Field(default=.35, gt=0, lt=1)
    burst_seconds: float = Field(default=1, ge=.3, le=2)
    poll_seconds: float = Field(default=.2, ge=.1, le=.3)
    max_seconds: float = Field(default=36000, ge=1, le=36000)
    critical_hp: float = Field(default=.35, gt=0, lt=1)
    danger_hp: float = Field(default=.5, gt=0, lt=1)
    heal_hp: float = Field(default=.8, gt=0, lt=1)
    rapid_drop: float = Field(default=.08, gt=0, le=1)
    low_mp: float = Field(default=.2, gt=0, lt=1)
    minimum_cast_mp: float = Field(default=.02, ge=0, lt=1)
    quiet_seconds: float = Field(default=30, ge=30, le=60)
    chase_seconds: float = Field(default=30, ge=0, le=30)
    chase_probe_seconds: float = Field(default=3, ge=.3, le=10)
    exclude_seconds: float = Field(default=15, ge=1, le=120)
    retry_seconds: float = Field(default=5, ge=1, le=30)
    verify_seconds: float = Field(default=.3, ge=.1, le=3)
    allow_ore: bool = True
    balance_key: str = "1"
    battle_key: str = "2"
    major_key: str = "3"
    mass_major_key: str = "F2"
    group_key: str = "F6"
    group_buff_seconds: float = Field(default=15, ge=15, le=60)
    avatar_key: str = "NUM7"
    invocation_key: str = "-"
    recharge_key: str | None = None
    purify_key: str = "F4"
    purify_base_cast: float = Field(default=3, gt=0, le=30)
    purify_cooldown: float = Field(default=4, gt=0, le=60)
    purify_cast: float | None = Field(default=None, gt=0, le=30)
    self_key: str | None = None
    self_rescue_key: str | None = "4"
    self_rescue_cast: float = Field(default=.2, ge=0, le=30)
    self_rescue_cooldown: float = Field(default=8, ge=8, le=60)
    aura_enabled: bool = True
    aura_key: str = "F8"
    aura_cast: float = Field(default=.3, ge=0, le=3)
    balance_cooldown: float = Field(default=30, ge=30, le=3600)
    avatar_cooldown: float = Field(default=1200, ge=1200, le=3600)
    invocation_cooldown: float = Field(default=300, ge=300, le=3600)
    casting_speed: float = Field(default=850, ge=100, le=3000)
    shot_multiplier: float = Field(default=1, gt=0, le=1)
    cast_margin: float = Field(default=.25, ge=.1, le=3)
    # Базы aCis — настраиваемый ориентир, а не подтверждённые тайминги LU4.
    balance_base_cast: float = Field(default=5, gt=0, le=30)
    battle_base_cast: float = Field(default=2, gt=0, le=30)
    major_base_cast: float = Field(default=5, gt=0, le=30)
    mass_major_base_cast: float = Field(default=7, gt=0, le=30)
    group_base_cast: float = Field(default=7, gt=0, le=30)
    avatar_base_cast: float = Field(default=4, gt=0, le=30)
    invocation_base_cast: float = Field(default=2, gt=0, le=30)
    recharge_base_cast: float = Field(default=6, gt=0, le=30)
    # Явные измеренные ожидания имеют приоритет над расчётной оценкой.
    balance_cast: float | None = Field(default=None, gt=0, le=30)
    battle_cast: float | None = Field(default=None, gt=0, le=30)
    major_cast: float | None = Field(default=None, gt=0, le=30)
    mass_major_cast: float | None = Field(default=None, gt=0, le=30)
    group_cast: float | None = Field(default=None, gt=0, le=30)
    avatar_cast: float | None = Field(default=None, gt=0, le=30)
    recharge_cast: float | None = Field(default=None, gt=0, le=30)
    regular_cooldown: float = Field(default=2, ge=.1, le=60)

    @field_validator("balance_key", "battle_key", "major_key", "mass_major_key", "group_key",
                     "avatar_key", "invocation_key", "recharge_key", "self_key", "purify_key", "follow_key", "mp_buff_key", "mp_buff2_key", "stun_probe_key", "loot_key", "self_rescue_key", "aura_key")
    @classmethod
    def allowed_key(cls, value: str | None) -> str | None:
        if value is not None and value not in KEYS:
            raise ValueError("Клавиша не разрешена контроллером")
        return value

    @model_validator(mode="after")
    def ordered(self) -> "HealerConfig":
        if not self.critical_hp < self.danger_hp < self.heal_hp:
            raise ValueError("Нужно critical_hp < danger_hp < heal_hp")
        if self.minimum_cast_mp >= self.low_mp:
            raise ValueError("minimum_cast_mp должен быть ниже low_mp")
        return self

    def cast_seconds(self, skill: Skill) -> float:
        if skill in {"mp_buff", "mp_buff2", "self_rescue"}:
            return getattr(self, skill + "_cast")
        if skill == "invocation":
            # 30 секунд эффекта не ускоряются casting speed; startup оставляем базовым.
            return 30 + self.invocation_base_cast + self.cast_margin
        override = getattr(self, skill + "_cast", None)
        if override is not None:
            return override
        base = getattr(self, skill + "_base_cast")
        estimate = max(.5, base * 333 / self.casting_speed * self.shot_multiplier) + self.cast_margin
        return estimate

    def cooldown(self, skill: Skill) -> float:
        if skill == "purify":
            return max(.5, self.purify_cooldown * 333 / self.casting_speed)
        return getattr(self, skill + "_cooldown", self.regular_cooldown)


class HealerSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)
    at: float
    hp: float | None = Field(default=None, ge=0, le=1)
    mp: float | None = Field(default=None, ge=0, le=1)
    cp: float | None = Field(default=None, ge=0, le=1)
    members: tuple[PartyMember, ...] = ()
    roster_known: bool = False
    destroyer_slots: tuple[int, ...] = ()
    profile_revision: int = 0
    selected_name: str | None = None
    selected_class: str | None = None
    selected_clan: str | None = None
    selected_confidence: float = Field(default=0, ge=0, le=1)

    def follows_selected(self, target: str) -> bool:
        matches = [member for member in self.members if member.target_id == target]
        return (len(matches) == 1 and matches[0].confidence >= .9
                and self.selected_confidence >= .9 and bool(self.selected_class)
                and bool(self.selected_name) and matches[0].name == self.selected_name
                and sum(member.name == self.selected_name for member in self.members) == 1)

    def selected_other_player(self, target: str) -> bool:
        leaders = [member for member in self.members if member.target_id == target]
        return (len(leaders) == 1 and bool(leaders[0].name) and leaders[0].confidence >= .9
                and self.selected_confidence >= .9 and bool(self.selected_class)
                and bool(self.selected_name) and self.selected_name != leaders[0].name)

    def health(self) -> dict[str, float]:
        result = {m.target_id: m.hp for m in self.members if m.hp is not None}
        if self.hp is not None:
            result["@self"] = self.hp
        return result


class HealDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["cast", "follow", "chase", "wait", "stop", "probe"]
    skill: Skill | None = None
    target: str | None = None
    reason: str


class HealPolicy:
    def __init__(self, config: HealerConfig, now: float) -> None:
        self.config = config
        self.humanize = False
        self.chain_heals = False
        self.heal_attempt_since: float | None = None
        self.heal_attempt_targets: set[str] = set()
        self.recovery_follow = False
        self.destroyer_slots: tuple[int, ...] = ()
        self.follow_name: str = f"@slot{config.follow_slot}"
        self.follow_needed = True
        self.next_follow_at = now + config.follow_seconds
        self.ready_at: dict[str, float] = {}
        self.busy_until = now
        self.last_damage = now
        self.self_safe_since: float | None = None
        self.self_attempt_since: float | None = None
        self.self_attempt_count = 0
        self.support_ready_at = now + config.support_start_delay
        self.hp_missing_since: dict[int, float] = {}
        self.expected_slots: set[int] = {config.follow_slot}
        self.ignored_slots: set[int] = set()
        self.roster_warning: str | None = None
        self.previous: HealerSnapshot | None = None
        self.history: dict[str, deque[tuple[float, float]]] = {}
        self.excluded: dict[str, float] = {}
        self.retry_at: dict[str, float] = {}
        self.single_failed: set[str] = set()
        self.group_failed = False
        self.last_balance = float("-inf")
        self.pending: tuple[HealDecision, dict[str, float], float] | None = None
        self.chase_name: str | None = None
        self.chase_until = now
        self.probe_at = now
        self.feedback_note: str | None = None
        self.stun_suspected = False
        self.probe_started_at: float | None = None
        self.probe_baseline: dict[str, float] = {}
        self.probe_frame_at: float | None = None

    def rate(self, name: str) -> float:
        history = self.history.get(name)
        if not history or len(history) < 2:
            return 0.
        end = history[-1]
        return max([0.] + [(hp - end[1]) / (end[0] - at) for at, hp in history
                          if end[0] - at >= .3])

    def ingest(self, state: HealerSnapshot) -> None:
        if self.previous is not None and state.at <= self.previous.at:
            return
        if self.previous is not None and state.profile_revision != self.previous.profile_revision:
            self.previous = None
            self.history.clear()
            self.hp_missing_since.clear()
            self.ignored_slots.clear()
            self.expected_slots = {member.slot for member in state.members}
            self.heal_attempt_since = None
            self.follow_needed = True
        if state.destroyer_slots != self.destroyer_slots and state.destroyer_slots:
            logger.warning("Иконка Разрушителя на строках %s: баланс запрещён", state.destroyer_slots)
        self.destroyer_slots = state.destroyer_slots
        self.expected_slots.update(member.slot for member in state.members if member.slot not in self.ignored_slots)
        observed = {member.slot: member.hp for member in state.members}
        for slot in self.expected_slots:
            if observed.get(slot) is None:
                self.hp_missing_since.setdefault(slot, state.at)
            else:
                self.hp_missing_since.pop(slot, None)
        expired = {slot for slot, since in self.hp_missing_since.items()
                   if state.at - since >= self.config.missing_hp_timeout}
        if expired:
            self.ignored_slots.update(expired)
            self.expected_slots.difference_update(expired)
            for slot in expired:
                self.hp_missing_since.pop(slot, None)
            self.roster_warning = ("WARNING: исключены строки " + ", ".join(map(str, sorted(expired)))
                                   + "; нужно переснять через OCR новый состав пати")
            logger.warning(self.roster_warning)
            if self.follow_name in {f"@slot{slot}" for slot in expired}:
                candidates = [member.slot for member in state.members
                              if member.slot not in self.ignored_slots and member.hp is not None]
                if candidates:
                    self.follow_name = f"@slot{min(candidates)}"
                    self.follow_needed = True
        state = self.monitored_state(state)
        health = state.health()
        previous = self.previous
        if (previous is None or state.hp is None or previous.hp is None or state.at - previous.at > 1
                or state.hp - previous.hp >= .005 or state.hp >= self.config.maintain_hp):
            self.self_attempt_since = None
            self.self_attempt_count = 0
            if not self.stun_suspected:
                self.probe_started_at = None
                self.probe_frame_at = None
                self.probe_baseline.clear()
        if (state.hp is None or previous is None or previous.hp is None
                or not 0 < state.at - previous.at <= .75):
            self.self_safe_since = state.at if state.hp is not None else None
        elif (previous.hp - state.hp) / (state.at - previous.at) > .05 + 1e-9:
            self.self_safe_since = state.at
        if self.chain_heals and self.heal_attempt_since is not None:
            previous_health = self.previous.health() if self.previous is not None else {}
            helped = any(target in health and target in previous_health
                         and health[target] - previous_health[target] >= .005
                         for target in self.heal_attempt_targets)
            gap = self.previous is not None and state.at - self.previous.at > 1
            if helped or gap or all(health.get(target, 0) >= self.config.maintain_hp for target in self.heal_attempt_targets):
                self.heal_attempt_since = None
                self.heal_attempt_targets.clear()
                self.recovery_follow = False
            elif state.at - self.heal_attempt_since >= 30:
                if not self.recovery_follow:
                    logger.warning("Хил не дал наблюдаемого эффекта за 30 секунд; восстановить подход к группе")
                self.recovery_follow = True
                self.follow_needed = True
                self.feedback_note = "Нет эффекта хила 30 секунд: возможны отрыв от группы, стан или входящий урон; восстановить подход"
        if self.stun_suspected and self.probe_frame_at is not None and state.at > self.probe_frame_at:
            if state.at - self.probe_frame_at > .75 or set(health) != set(self.probe_baseline):
                # После пропуска кадров не приписываем накопившийся прирост нажатию Пробела.
                self.probe_baseline = health.copy()
            elif any(hp - self.probe_baseline.get(target, hp) >= self.config.stun_probe_effect
                     for target, hp in health.items()):
                self.stun_suspected = False
                self.probe_started_at = None
                self.probe_baseline.clear()
                self.feedback_note = "После Пробела замечен прирост HP; возвращаемся к обычному лечению"
                self.single_failed.clear()
                self.group_failed = False
            self.probe_frame_at = state.at if self.stun_suspected else None
            if self.stun_suspected:
                self.probe_baseline = {target: min(hp, self.probe_baseline.get(target, hp))
                                       for target, hp in health.items()}
        if self.previous is None:
            # Время загрузки OCR до первого кадра не считается затишьем в пати.
            self.last_damage = state.at
        if (not self.health_complete(state) or state.hp is None or state.cp is None
                or any(member.cp is None for member in state.members)):
            self.last_damage = state.at
        if self.previous is not None:
            if state.at - self.previous.at > .75:
                self.last_damage = state.at
            old_hp = self.previous.health()
            old_cp = {m.target_id: m.cp for m in self.previous.members}
            old_cp["@self"] = self.previous.cp
            current_cp = {m.target_id: m.cp for m in state.members}
            current_cp["@self"] = state.cp
            if set(old_hp) != set(health):
                self.last_damage = state.at
            for current, old in ((health, old_hp), (current_cp, old_cp)):
                if any(v is not None and old.get(n) is not None and old[n] - v > .002
                       for n, v in current.items()):
                    self.last_damage = state.at
        self.history = {name: history for name, history in self.history.items() if name in health}
        for name, hp in health.items():
            history = self.history.setdefault(name, deque(maxlen=32))
            if history and state.at - history[-1][0] > .75:
                history.clear()
            history.append((state.at, hp))
            while len(history) > 2 and history[0][0] < state.at - 2:
                history.popleft()
        self.previous = state
        if self.pending and state.at >= self.pending[2]:
            decision, before, _ = self.pending
            self.pending = None
            if self.chain_heals:
                return
            if decision.skill in {"battle", "major"}:
                name = decision.target
                if name in before and name in health:
                    if health[name] < before[name] + .015:
                        self.single_failed.add(name)
                        self.stun_suspected = True
                        self.feedback_note = "Нет заметного эффекта хила: возможен стан на хилере (3–7 с), входящий урон или дальность; дебафф не подтверждён"
                    else:
                        self.single_failed.discard(name)
                        self.excluded.pop(name, None)
                        self.feedback_note = None
            if decision.skill in {"group", "mass_major", "avatar"}:
                affected = [name for name in before if before[name] < self.config.heal_hp and name in health]
                helped = [name for name in affected if health[name] >= before[name] + .015]
                self.group_failed = bool(affected) and not helped
                if self.group_failed:
                    self.stun_suspected = True
                    self.feedback_note = "Нет заметного эффекта масс-хила: возможен стан на хилере (3–7 с), входящий урон или дальность; дебафф не подтверждён"
                elif helped:
                    self.feedback_note = None
                # Отсутствие эффекта — гипотеза недоступности, не доказанный range.
                for name in affected:
                    if name == "@self":
                        continue
                    if name in helped:
                        self.excluded.pop(name, None)
                        self.single_failed.discard(name)
                    elif helped:
                        self.excluded[name] = state.at + self.config.exclude_seconds

    def ready(self, skill: Skill, now: float) -> bool:
        if skill == "balance" and self.destroyer_slots:
            return False
        return now >= self.ready_at.get(skill, 0)

    def health_complete(self, state: HealerSnapshot) -> bool:
        # Нечитаемый MP не отменяет достоверные HP видимых строк пати.
        if self.hp_missing_since:
            return False
        return bool(state.members) and all(member.hp is not None for member in state.members) and (
            state.roster_known or bool(self.ignored_slots) or any(member.mp is None for member in state.members))

    def monitored_state(self, state: HealerSnapshot) -> HealerSnapshot:
        members = tuple(member for member in state.members if member.slot not in self.ignored_slots)
        return state.model_copy(update={"members": members,
                                      "roster_known": state.roster_known or (
                                          bool(self.ignored_slots) and bool(members)
                                          and all(member.hp is not None for member in members))})

    def emergency_follow(self, state: HealerSnapshot, now: float) -> HealDecision | None:
        if not (self.follow_needed or now >= self.next_follow_at):
            return None
        if (not self.config.follow_select_mouse and self.config.follow_key is not None
                or any(member.target_id == self.follow_name for member in state.members)):
            return HealDecision(kind="follow", target=self.follow_name, reason="Следование в аварийном режиме")
        return None

    def stun_probe_decision(self, state: HealerSnapshot, now: float) -> HealDecision:
        if self.probe_started_at is not None and now >= self.probe_started_at + self.config.stun_probe_timeout:
            return HealDecision(kind="stop", reason="Пробел не дал наблюдаемого эффекта за лимит восстановления")
        return HealDecision(kind="probe", reason="Подозрение на стан: удерживать Пробел до прироста HP")

    def begin_stun_probe(self, state: HealerSnapshot, now: float) -> None:
        if self.probe_started_at is None:
            self.probe_started_at = now
        if self.probe_frame_at is None:
            self.probe_baseline = state.health().copy()
            self.probe_frame_at = state.at

    def strong_damage(self, target: str, threshold: float | None = None) -> bool:
        history = self.history.get(target)
        if not history or len(history) < 3:
            return False
        limit = self.config.single_damage if threshold is None else threshold
        samples = list(history)[-3:]
        return all(0 < b[0] - a[0] <= .75 and (a[1] - b[1]) / (b[0] - a[0]) > limit + 1e-9
                   for a, b in pairwise(samples))

    def needs_single(self, state: HealerSnapshot, target: str) -> bool:
        if self.destroyer_protected(state, target):
            return False
        hp = state.health().get(target)
        return hp is not None and (hp < self.config.maintain_hp
                                   or self.strong_damage(target))

    def needs_battle(self, state: HealerSnapshot, target: str) -> bool:
        if self.destroyer_protected(state, target):
            return False
        hp = state.health().get(target)
        return hp is not None and hp < .5 and self.strong_damage(target)

    def destroyer_protected(self, state: HealerSnapshot, target: str) -> bool:
        return target in {f"@slot{slot}" for slot in state.destroyer_slots}

    def recent_drop(self, target: str, now: float) -> float:
        history = self.history.get(target)
        if not history:
            return 0.
        latest_hp = history[-1][1]
        return max([0.] + [hp - latest_hp for at, hp in history
                          if 0 < now - at <= self.config.burst_seconds])

    def needs_balance(self, health: dict[str, float]) -> bool:
        lowest = sorted(health, key=health.get)
        for count in range(1, min(3, len(lowest) - 1) + 1):
            rest = lowest[count:]
            average = sum(health[target] for target in rest) / len(rest)
            if all(average - health[target] > self.config.balance_gap + 1e-9
                   for target in lowest[:count]) and all(
                       average - health[target] <= self.config.balance_gap + 1e-9 for target in rest):
                return True
        return False

    def cast(self, skill: Skill, reason: str, target: str | None = None) -> HealDecision:
        return HealDecision(kind="cast", skill=skill, target=target, reason=reason)

    def emergency_slots(self, now: float) -> tuple[int, ...]:
        return tuple(sorted(slot for slot, since in self.hp_missing_since.items() if now - since >= 15))

    def recovery_decision(self, state: HealerSnapshot, now: float) -> HealDecision:
        follow = self.emergency_follow(state, now)
        if follow is not None:
            return follow.model_copy(update={"reason": "Хил без эффекта 30 секунд: восстановить подход к группе"})
        return HealDecision(kind="wait", reason="Хил без эффекта 30 секунд; нет распознанного ведущего для подхода")

    def urgent_invocation(self, state: HealerSnapshot, now: float) -> bool:
        return (state.mp is not None and state.mp < self.config.invocation_mp
                and state.hp is not None and state.hp > 0 and self.health_complete(state)
                and state.at - self.last_damage >= self.config.quiet_seconds
                and 0 <= now - state.at <= .5)

    def self_rescue_decision(self, state: HealerSnapshot, now: float) -> HealDecision | None:
        if self.config.self_rescue_key is None:
            return None
        failed = (self.self_attempt_since is not None and self.self_attempt_count >= 2
                  and now - self.self_attempt_since >= 2)
        if (self.config.self_rescue_key and self.ready("self_rescue", now) and failed
                and state.hp is not None and 0 < state.hp < self.config.maintain_hp
                and state.mp is not None and state.mp >= self.config.minimum_cast_mp):
            return self.cast("self_rescue", "Спасение себя: повторные хилы не дали наблюдаемого прироста HP")
        if failed and state.hp is not None and 0 < state.hp < .5:
            if self.probe_started_at is not None and now - self.probe_started_at >= self.config.stun_probe_timeout:
                self.self_attempt_since = None
                self.self_attempt_count = 0
                self.probe_started_at = None
                self.probe_frame_at = None
                self.recovery_follow = True
                self.follow_needed = True
                return self.recovery_decision(state, now)
            return HealDecision(kind="probe", reason="Спасение себя Пробелом: другие хилы не дали эффекта, скилл 4 в откате")
        return None

    def mp_buff_decision(self, now: float) -> HealDecision | None:
        if now < self.support_ready_at or self.config.mp_buff_key is None:
            return None
        for skill in ("mp_buff", "mp_buff2"):
            if getattr(self.config, skill + "_key") and self.ready(skill, now):
                return self.cast(skill, "Селф-баф MP по откату: " + getattr(self.config, skill + "_key"))
        return None

    def emergency_balance(self, state: HealerSnapshot, now: float) -> HealDecision | None:
        slots = self.emergency_slots(now)
        if not slots:
            return None
        reason = "Аварийный режим: HP не читается 15 секунд; строки " + ", ".join(map(str, slots))
        if state.mp >= self.config.minimum_cast_mp and self.ready("balance", now):
            return self.cast("balance", reason)
        follow = self.emergency_follow(state, now)
        if follow is not None:
            return follow
        return HealDecision(kind="wait", reason=reason + "; ожидание отката баланса / MP")

    def decide(self, state: HealerSnapshot, now: float) -> HealDecision:
        self.ingest(state)
        state = self.monitored_state(state)
        cfg = self.config
        if 0 <= now - state.at <= 1 and state.hp == 0:
            return HealDecision(kind="stop", reason="Собственное HP = 0")
        if now < self.busy_until:
            return HealDecision(kind="wait", reason="Каст / Invocation")
        if now - state.at > 1 or state.at > now:
            return HealDecision(kind="wait", reason="Нет свежего наблюдения")
        if state.hp is None or state.mp is None:
            return HealDecision(kind="wait", reason="Собственные HP/MP неизвестны")
        if state.hp == 0:
            return HealDecision(kind="stop", reason="Собственное HP = 0")
        if self.recovery_follow:
            return self.recovery_decision(state, now)
        rescue = self.self_rescue_decision(state, now)
        if rescue is not None:
            return rescue
        if self.stun_suspected:
            return self.stun_probe_decision(state, now)
        emergency = self.emergency_balance(state, now)
        if emergency is not None:
            return emergency
        health = state.health()
        can_heal = state.mp >= cfg.minimum_cast_mp
        all_known = self.health_complete(state)
        fast_losses = [name for name, hp in health.items()
                       if hp < cfg.heal_hp and self.strong_damage(name, cfg.rapid_drop)]
        avatar_needed = (all_known and len(health) >= 2 and len(fast_losses) >= 2
                         and ((now - self.last_balance <= 10 and max(health.values()) <= .6)
                              or (self.group_failed and len(fast_losses) >= 2)))
        # Для массового урона учитываем всю наблюдаемую группу, включая себя.
        if all_known and len(health) >= 2 and can_heal:
            drops = [self.recent_drop(target, now) for target in health]
            severe = sum(self.recent_drop(target, now) > .33 + 1e-9 and self.strong_damage(target)
                         for target in health) / len(health) > .7
            if severe:
                if self.ready("group", now):
                    return self.cast("group", "Массовый урон: обычный масс-хил первым")
                if cfg.allow_ore and self.ready("mass_major", now):
                    return self.cast("mass_major", "Более 70% группы потеряли более 33% HP")
                if self.ready("group", now):
                    return self.cast("group", "Массовый урон; мажор групп хил недоступен")
            elif sum(drop > .15 + 1e-9 for drop in drops) / len(drops) > .7:
                if self.ready("group", now):
                    return self.cast("group", "Более 70% группы потеряли более 15% HP")
            if self.needs_balance(health) and self.ready("balance", now):
                return self.cast("balance", f"HP 1–3 участников ниже среднего остальных более чем на {cfg.balance_gap:.0%}")
        heal_targets = [target for target in health if self.needs_single(state, target)]
        if can_heal and len(heal_targets) > 2 and self.ready("group", now):
            return self.cast("group", "Три и более цели нуждаются в лечении до 70% HP")
        # При угрозе себе привязка откладывается до лечения, массовый хил также лечит себя.
        if self.needs_single(state, "@self") and can_heal:
            if avatar_needed and self.ready("avatar", now):
                return self.cast("avatar", "Спасение себя и группы после баланса / массового урона")
            if self.needs_battle(state, "@self") and self.ready("battle", now):
                return self.cast("battle", "Собственное HP в приоритете", "@self")
            if cfg.allow_ore and self.ready("major", now):
                return self.cast("major", "Собственное HP низкое; условия батл хила не выполнены", "@self")
            if cfg.allow_ore and state.hp < cfg.danger_hp and self.ready("mass_major", now):
                return self.cast("mass_major", "Опасность для себя; батл хил недоступен")
            if self.ready("group", now):
                return self.cast("group", "Собственное лечение без условий для батл хила")
            return HealDecision(kind="wait", reason="Ожидание собственного лечения")
        if (self.config.follow_select_mouse or self.config.follow_key is None) and self.follow_name not in state.health():
            return HealDecision(kind="wait", reason="HP строки ведущего не распознано")
        if self.chase_name is not None:
            if now >= self.chase_until:
                return HealDecision(kind="follow", target=self.follow_name, reason="Лимит сближения")
            if now < self.probe_at:
                return HealDecision(kind="wait", reason="Ограниченное сближение")
            name = self.chase_name
            if self.needs_battle(state, name) and can_heal and self.ready("battle", now):
                return self.cast("battle", "Проверка лечения после сближения", name)
            return HealDecision(kind="follow", target=self.follow_name, reason="Цель сближения неизвестна / мало MP")
        health_known = self.health_complete(state)
        buff = self.mp_buff_decision(now)
        buff_ready = buff is not None and not heal_targets and health_known
        if buff_ready and health_known and can_heal:
            # Короткий баф выполняем до Invocation, который надолго блокирует другие касты.
            return buff
        if (now >= self.support_ready_at and self.urgent_invocation(state, now) and health_known
                and all(m.hp is not None and m.hp >= cfg.maintain_hp for m in state.members)
                and state.hp >= cfg.maintain_hp and now - self.last_damage >= cfg.quiet_seconds
                and self.ready("invocation", now)):
            return self.cast("invocation", "Мало MP, входящего урона нет")
        if not can_heal:
            follow = self.emergency_follow(state, now)
            if follow is not None:
                return follow
            return HealDecision(kind="wait", reason="MP ниже резерва; ожидается безопасный Invocation")
        active = {name: hp for name, hp in health.items() if name != "@self"
                  and now >= self.excluded.get(name, 0)}
        injured = {name: hp for name, hp in active.items() if self.needs_single(state, name)}
        danger = {name: hp for name, hp in injured.items()
                  if hp < cfg.danger_hp or self.strong_damage(name, cfg.rapid_drop)}
        if self.ready("avatar", now) and avatar_needed:
            return self.cast("avatar", "После баланса HP ≤60% / массовое лечение не справляется")
        failed_single = any(name in self.single_failed for name in injured)
        if failed_single:
            if self.ready("group", now):
                return self.cast("group", "Одиночный хил не справился: обычный масс-хил первым")
            if danger and cfg.allow_ore and self.ready("mass_major", now):
                return self.cast("mass_major", "Опасность / одиночный хил не дал эффекта")
            if self.ready("group", now):
                return self.cast("group", "Нужен массовый хил без руды")
            if failed_single:
                return HealDecision(kind="wait", reason="Ожидание массового хила после неудачного одиночного")
        if injured:
            name = min(injured, key=lambda n: (injured[n] - cfg.critical_hp) / max(.01, self.rate(n)))
            if self.needs_battle(state, name) and self.ready("battle", now):
                return self.cast("battle", "Одиночное лечение", name)
            if cfg.allow_ore and self.ready("major", now):
                return self.cast("major", "Опасность; батл хил недоступен или не требуется", name)
            if self.ready("group", now):
                return self.cast("group", "Лечение без условий для батл хила")
        for name, until in self.excluded.items():
            if (name in health and now < until and now >= self.retry_at.get(name, 0)
                    and self.strong_damage(name, cfg.rapid_drop)):
                if cfg.chase_seconds > 0:
                    return HealDecision(kind="chase", target=name, reason="У исключённого участника быстро падает HP")
                if self.needs_battle(state, name) and self.ready("battle", now):
                    return self.cast("battle", "Повторная попытка при резком падении HP", name)
        if self.follow_needed or now >= self.next_follow_at:
            return HealDecision(kind="follow", target=self.follow_name, reason="Подойти к ведущему на дистанцию 400")
        if buff_ready:
            return buff
        if not injured and cfg.recharge_key and self.ready("recharge", now) and state.mp >= .5:
            candidates = [m for m in state.members if m.target_id in active and m.mp is not None and m.mp < .8]
            if candidates:
                return self.cast("recharge", "MP участника ниже 80%", min(candidates, key=lambda m: m.mp).target_id)
        return HealDecision(kind="wait", reason="Наблюдение пати")

    def committed(self, decision: HealDecision, state: HealerSnapshot, now: float) -> None:
        if decision.kind == "cast":
            skill = decision.skill
            chained = self.chain_heals and skill not in {"invocation", "mp_buff", "mp_buff2"}
            self.busy_until = now + (self.config.heal_press_interval if chained else self.config.cast_seconds(skill) + self.config.verify_seconds)
            self.ready_at[skill] = now + (self.config.cooldown(skill) if chained else max(self.config.cooldown(skill), self.config.cast_seconds(skill)))
            if skill == "group":
                # Откладываем повтор обычного масс-хила до окончания его баффа.
                self.ready_at[skill] = max(self.ready_at[skill], now + self.config.cast_seconds(skill)
                                           + self.config.group_buff_seconds)
            if chained and skill in {"balance", "battle", "major", "group", "mass_major", "avatar", "self_rescue"}:
                targets = {decision.target} if decision.target else {target for target, hp in state.health().items()
                                                                    if hp < self.config.maintain_hp}
                if targets:
                    if self.heal_attempt_since is None:
                        self.heal_attempt_since = now
                    self.heal_attempt_targets.update(targets)
            if skill in {"battle", "major", "group", "mass_major", "avatar", "balance"} and (
                    decision.target == "@self" or decision.target is None and state.hp is not None
                    and state.hp < self.config.maintain_hp):
                if self.self_attempt_since is None:
                    self.self_attempt_since = now
                self.self_attempt_count += 1
            if self.humanize and skill in {"mp_buff", "mp_buff2"}:
                self.ready_at[skill] += random.uniform(0, 5)
            self.pending = decision, state.health(), self.busy_until
            if skill not in {"mp_buff", "mp_buff2"} and self.config.follow_key is None:
                self.follow_needed = True
            self.chase_name = None
            if skill == "balance":
                self.last_balance = self.busy_until
            if decision.target:
                self.retry_at[decision.target] = now + self.config.retry_seconds
        elif decision.kind == "follow":
            if self.recovery_follow:
                self.recovery_follow = False
                self.heal_attempt_since = None
                self.heal_attempt_targets.clear()
                self.self_attempt_since = None
                self.self_attempt_count = 0
                self.probe_started_at = None
                self.probe_frame_at = None
                self.feedback_note = "Команда подхода к группе отправлена; продолжить лечение и проверку эффекта"
            self.follow_needed = False
            interval = self.config.follow_seconds * (random.uniform(.9, 1.1) if self.humanize else 1)
            self.next_follow_at = now + interval
            if self.chase_name:
                self.excluded[self.chase_name] = now + self.config.exclude_seconds
            self.chase_name = None
        elif decision.kind == "chase":
            self.chase_name = decision.target
            self.chase_until = now + self.config.chase_seconds
            self.probe_at = now + min(self.config.chase_probe_seconds, self.config.chase_seconds)
            self.retry_at[decision.target] = self.chase_until + self.config.exclude_seconds
