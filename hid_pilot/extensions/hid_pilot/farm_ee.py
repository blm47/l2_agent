"""
Поддержка пати ЕЕ: лечение, заливка MP и персональные таймеры бафов.
"""

import json
import math
import random
import time
from collections.abc import Callable
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from hid_pilot.auto_loot import AutoLoot
from hid_pilot.bars import BarProfile
from hid_pilot.controller import ActionController
from hid_pilot.healer import HealerConfig, HealerSnapshot
from hid_pilot.healer_runtime import Healer, TargetChanged, logger, prepare_extension
from hid_pilot.party import PartyProfile
from hid_pilot.script import KEYS


class EEConfig(HealerConfig):
    mana_block_messages: tuple[str, ...] = (
        "Recharge cannot be used due to unsuitable terms",
        "Rechargecannotbeused dduetounsuitable eterms",
        "Rechardge cannot be used to unsituable terns",
    )
    mana_block_seconds: float = Field(default=60, ge=5, le=600)
    circlet_of_heal_enabled: bool = True
    circlet_of_heal_hp: float = Field(default=.30, gt=0, le=1)
    circlet_of_heal_min_members: int = Field(default=3, ge=1, le=9)
    circlet_of_heal_cast: float = Field(default=.5, gt=0, le=30)
    circlet_of_heal_cooldown: float = Field(default=24, gt=0, le=3600)
    heal_hp_threshold: float = Field(default=.20, gt=0, le=1)
    self_heal_hp_threshold: float = Field(default=.75, gt=0, le=1)
    party_mp_threshold: float = Field(default=.30, gt=0, le=1)
    single_key: str = "2"
    mana_key: str = "3"
    resurrect_key: str = "F1"
    resurrect_cooldown: float = Field(default=20, gt=0, le=3600)
    invocation_single_cast: float = Field(default=1.5, gt=0, le=120)
    invocation_party_cast: float = Field(default=1.5, gt=0, le=120)
    invocation_at_five: str = "party"
    buff_cast: float = Field(default=2, gt=0, le=30)
    party_buffs_on_start: bool = False
    follow_key: str = "7"
    humanize: bool = False
    random_movement: bool = True

    @field_validator("mana_block_messages", mode="before")
    @classmethod
    def exact_mana_messages(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split("|") if part.strip())
        return value

    @field_validator("single_key", "mana_key", "resurrect_key")
    @classmethod
    def valid_ee_key(cls, value: str) -> str:
        if value not in KEYS:
            raise ValueError("Клавиша ЕЕ не разрешена контроллером")
        return value

    @field_validator("invocation_at_five")
    @classmethod
    def valid_five(cls, value: str) -> str:
        if value not in {"single", "party"}:
            raise ValueError("Для пяти участников укажите single или party")
        return value


class EEDecision(BaseModel):
    skill: str
    target: str | None = None


@dataclass
class ManaEffectCheck:
    name: str
    before: float
    sent_at: float
    check_after: float
    revision: int
    generation: int
    last_at: float
    samples: int = 0


class EEPolicy:
    def __init__(self, config: EEConfig, now: float | None = None) -> None:
        self.config = config
        started_at = time.monotonic() if now is None else now
        self.party_buffs_ready_at = started_at + (0 if config.party_buffs_on_start else 300)
        self.ready_at: dict[str, float] = {}
        self.busy_until = 0.
        self.next_follow = 0.
        self.next_move = started_at + random.uniform(30, 60)
        self.previous_damage: HealerSnapshot | None = None
        self.damage_rates: dict[str, float] = {}
        self.high_damage_since: dict[str, float] = {}
        self.damage_ignore_before = float("-inf")
        self.buff_members: dict[str, str] = {}
        self.emergency_mana_used: set[str] = set()
        self.mana_quota_dirty = False
        self.mana_chat_blocked_names: set[str] = set()
        self.mana_no_effect_until: dict[str, float] = {}

    @staticmethod
    def name_key(name: str | None) -> str:
        return (name or "").strip().casefold()

    def can_receive_mana(self, target: str, state: HealerSnapshot, skill: str = "mana") -> bool:
        if target == "@self":
            return True
        member = next((m for m in state.members if m.target_id == target), None)
        if member is None:
            return False
        name = self.name_key(member.name)
        return (name not in self.mana_chat_blocked_names
                and (skill != "mana" or state.at >= self.mana_no_effect_until.get(name, 0)))

    def sync_buff_members(self, state: HealerSnapshot) -> None:
        for member in state.members:
            target = member.target_id
            if not member.name or member.confidence < .9:
                continue
            previous = self.buff_members.get(target)
            # OCR ника не подтверждает смену участника и не сбрасывает откаты строки.
            self.buff_members[target] = member.name
            if previous is None:
                for skill in ("clarity", "shock", "gain"):
                    legacy = f"buff:{skill}:{member.name}"
                    if legacy in self.ready_at:
                        self.ready_at.setdefault(f"buff:{skill}:slot{member.slot}", self.ready_at[legacy])

    def reset_damage(self, after: float | None = None) -> None:
        self.previous_damage = None
        self.damage_rates.clear()
        self.high_damage_since.clear()
        if after is not None:
            self.damage_ignore_before = after

    def ingest(self, state: HealerSnapshot) -> None:
        if state.at <= self.damage_ignore_before:
            return
        previous = self.previous_damage
        if previous is not None and state.at <= previous.at:
            return
        self.damage_rates.clear()
        if (previous is None or state.profile_revision != previous.profile_revision
                or state.at - previous.at > 1):
            self.high_damage_since.clear()
        else:
            before = {m.target_id: (m.hp, m.name) for m in previous.members}
            current = {m.target_id: (m.hp, m.name) for m in state.members}
            before["@self"] = (previous.hp, "@self")
            current["@self"] = (state.hp, "@self")
            for target, (hp, name) in current.items():
                old_hp, old_name = before.get(target, (None, None))
                if hp is None or hp <= 0 or old_hp is None or old_hp <= 0 or name != old_name:
                    continue
                rate = max(0., (old_hp - hp) / (state.at - previous.at))
                self.damage_rates[target] = rate
                # 20% означает процентные пункты максимального HP в секунду.
                if rate > .2 and not math.isclose(rate, .2, abs_tol=1e-9):
                    self.high_damage_since.setdefault(target, previous.at)
                else:
                    self.high_damage_since.pop(target, None)
            for target in set(self.high_damage_since) - self.damage_rates.keys():
                del self.high_damage_since[target]
        self.previous_damage = state

    def buff_timer(self, skill: str, target: str, state: HealerSnapshot) -> str:
        if target == "@self":
            return f"buff:{skill}:self"
        member = next(m for m in state.members if m.target_id == target)
        # Строка позволяет учитывать бафы и при временно неизвестном имени.
        return f"buff:{skill}:slot{member.slot}"

    def buff_status(self, state: HealerSnapshot, now: float) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        self.sync_buff_members(state)
        targets = ["@self"] + [m.target_id for m in state.members]
        for target in targets:
            buffs = {}
            for skill, label in (("clarity", "Клара"), ("shock", "РШ"),
                                 ("gain", "Mana Gain"), ("kiss", "Kiss of Eva")):
                if skill == "gain" and not self.can_receive_mana(target, state, "gain"):
                    continue
                if skill == "kiss" and target != "@self":
                    continue
                timer = self.buff_timer(skill, target, state)
                if timer in self.ready_at:
                    buffs[label] = max(0, math.ceil(self.ready_at[timer] - now))
            result[target] = buffs
        return result

    def decide(self, state: HealerSnapshot, now: float) -> EEDecision | None:
        if not 0 <= now - state.at <= 1:
            self.reset_damage(after=now)
            return None
        self.ingest(state)
        self.mana_no_effect_until = {name: until for name, until in self.mana_no_effect_until.items()
                                    if until > now}
        self.sync_buff_members(state)
        if state.mp is not None and state.mp >= .10 and self.emergency_mana_used:
            self.emergency_mana_used.clear()
            self.mana_quota_dirty = True
        if (now < self.busy_until
                or state.hp is None or state.hp <= 0 or state.mp is None):
            return None
        ready = lambda skill: now >= self.ready_at.get(skill, 0)
        dead = [m for m in state.members if m.hp == 0]
        if dead and ready("resurrect"):
            return EEDecision(skill="resurrect", target=dead[0].target_id)
        living = [m for m in state.members if m.hp is not None and m.hp > 0]
        if state.mp < .05:
            recovery = self.mana_recovery(state, now)
            if recovery is not None:
                return recovery
            if state.hp < self.config.self_heal_hp_threshold and ready("single"):
                return EEDecision(skill="single", target="@self")
            if now >= self.next_follow and any(m.slot == self.config.follow_slot for m in living):
                return EEDecision(skill="follow")
            return None
        low = [m for m in living if m.hp < self.config.heal_hp_threshold]
        targets = [m.target_id for m in low if m.slot not in state.destroyer_slots]
        if state.hp < self.config.self_heal_hp_threshold:
            targets.insert(0, "@self")
        critical_count = sum(m.hp < self.config.circlet_of_heal_hp for m in living) + (state.hp < self.config.circlet_of_heal_hp)
        if (self.config.circlet_of_heal_enabled and critical_count >= self.config.circlet_of_heal_min_members
                and ready("circlet_of_heal")):
            return EEDecision(skill="circlet_of_heal")
        sustained_count = sum(state.at - since >= 2 - 1e-9
                              for since in self.high_damage_since.values())
        if (len(low) + (state.hp < self.config.heal_hp_threshold) >= 2
                or sustained_count > 2) and ready("group"):
            return EEDecision(skill="group")
        if targets and ready("single"):
            return EEDecision(skill="single", target=targets[0])
        recovery = self.mana_recovery(state, now)
        if recovery is not None:
            return recovery
        mana = sorted((m for m in living if m.mp is not None and self.can_receive_mana(m.target_id, state)),
                      key=lambda m: m.mp)
        if state.mp < .10:
            mana = [m for m in mana if m.slot in {1, 2} and m.mp < .02
                    and m.target_id not in self.emergency_mana_used]
        else:
            mana = [m for m in mana if m.mp < self.config.party_mp_threshold]
        if mana and ready("mana"):
            return EEDecision(skill="mana", target=mana[0].target_id)
        for skill in ("clarity", "shock", "gain", "kiss"):
            if not ready(skill):
                continue
            recipients = ["@self"]
            if skill != "kiss" and now >= self.party_buffs_ready_at:
                recipients += [m.target_id for m in living]
            for target in recipients:
                if skill == "gain" and not self.can_receive_mana(target, state, "gain"):
                    continue
                if now >= self.ready_at.get(self.buff_timer(skill, target, state), 0):
                    return EEDecision(skill=skill, target=None if skill == "kiss" else target)
        movement_safe = (state.roster_known and not low and not mana and state.mp >= .3
                         and state.hp >= self.config.self_heal_hp_threshold
                         and all(m.hp is not None and m.hp > 0 and m.mp is not None for m in state.members))
        if self.config.random_movement and now >= self.next_move and movement_safe:
            return EEDecision(skill="move")
        if now >= self.next_follow and any(m.slot == self.config.follow_slot for m in living):
            return EEDecision(skill="follow")
        return None

    def mana_recovery(self, state: HealerSnapshot, now: float) -> EEDecision | None:
        if state.mp is None:
            return None
        if state.mp < .3 and now >= self.ready_at.get("bless", 0):
            return EEDecision(skill="bless", target="@self")
        count = len(state.members) + 1
        kind = "single" if count < 5 else "party" if count > 5 else self.config.invocation_at_five
        # Четыре обнаруженные строки уже подтверждают минимум пять персонажей.
        size_sufficient = state.roster_known or (count >= 5 and kind == "party")
        low_mana_count = sum(m.hp is not None and m.hp > 0 and m.mp is not None and m.mp < .8
                             for m in state.members) + (state.mp < .8)
        invocation_needed = kind == "single" or low_mana_count >= 2
        if size_sufficient and invocation_needed and now >= self.ready_at.get("invoke_" + kind, 0):
            return EEDecision(skill="invoke_" + kind, target="@self" if kind == "single" else None)
        return None

    def timing(self, skill: str) -> tuple[str, float, float]:
        return {
            "single": (self.config.single_key, 1., 2.),
            "mana": (self.config.mana_key, 1., 2.),
            "group": ("F2", 1.5, 3.),
            "circlet_of_heal": (self.config.circlet_of_heal_key, self.config.circlet_of_heal_cast, self.config.circlet_of_heal_cooldown),
            "resurrect": (self.config.resurrect_key, 1., self.config.resurrect_cooldown),
            "bless": ("F9", 1.5, 1200.),
            "invoke_single": ("9", self.config.invocation_single_cast, 300.),
            "invoke_party": ("0", self.config.invocation_party_cast, 300.),
            "clarity": ("NUM7", self.config.buff_cast, 2.),
            "shock": ("NUM8", self.config.buff_cast, 2.),
            "gain": ("NUM0", self.config.buff_cast, 100.),
            "kiss": ("-", self.config.buff_cast, 5.),
        }[skill]


class EEFarmer(Healer):
    def __init__(self, controller: ActionController, own: BarProfile, party: PartyProfile,
                 config: EEConfig, notify: Callable[[str], None], state_path: Path) -> None:
        if controller.transport.protocol not in {"HID_PILOT_V3", "HID_PILOT_V4"}:
            raise ValueError("ЕЕ требует HID_PILOT_V3 для NUM8, NUM0, F9 и F10; ввод не начат")
        super().__init__(controller, own, party, config, notify, state_path)

    def latest(self) -> HealerSnapshot:
        state = super().latest()
        samples = self.observer.snapshots()
        for sample in samples:
            self.ee.ingest(sample)
        self.consume_mana_effects(samples)
        self.consume_mana_chat(state)
        return state

    def record_mana_effect(self, decision: EEDecision, state: HealerSnapshot, sent_at: float, cast: float) -> None:
        if decision.skill != "mana" or not 0 <= sent_at - state.at <= 1:
            return
        member = next((m for m in state.members if m.target_id == decision.target), None)
        if (member is None or not member.name or member.confidence < .9 or member.hp is None
                or member.hp <= 0 or member.mp is None or member.mp >= 1):
            return
        if not hasattr(self, "mana_effect_checks"):
            self.mana_effect_checks: dict[str, ManaEffectCheck] = {}
        name = self.ee.name_key(member.name)
        self.mana_effect_checks[name] = ManaEffectCheck(member.name, member.mp, sent_at, sent_at + cast + .5,
            state.profile_revision, self.controller.pause_generation, sent_at)

    def consume_mana_effects(self, samples: tuple[HealerSnapshot, ...]) -> None:
        checks = getattr(self, "mana_effect_checks", {})
        now = time.monotonic()
        for name, check in list(checks.items()):
            if check.generation != self.controller.pause_generation or now - check.sent_at > 5:
                del checks[name]
                continue
            for sample in samples:
                if sample.at <= check.last_at:
                    continue
                matches = [m for m in sample.members if self.ee.name_key(m.name) == name and m.confidence >= .9]
                if (sample.profile_revision != check.revision or sample.at - check.last_at > 1
                        or len(matches) != 1 or matches[0].hp is None or matches[0].hp <= 0
                        or matches[0].mp is None):
                    del checks[name]
                    break
                check.last_at = sample.at
                check.samples += 1
                if matches[0].mp > check.before:
                    del checks[name]
                    break
                if sample.at >= check.check_after and check.samples >= 2 and 0 <= now - sample.at <= 1:
                    self.ee.mana_no_effect_until[name] = now + 30
                    del checks[name]
                    self.report(f"ЕЕ: после заливки MP у {check.name} не выросло "
                                f"(до {check.before:.1%}, после {matches[0].mp:.1%}); пропуск заливки на 30 с")
                    break

    def record_mana_chat_attempt(self, decision: EEDecision, state: HealerSnapshot, sent_at: float) -> None:
        if (decision.skill not in {"mana", "gain"} or not self.config.mana_block_messages
                or not isinstance(getattr(self, "own", None), BarProfile)
                or not self.own.system_chat_enabled):
            return
        target = next((m for m in state.members if m.target_id == decision.target), None)
        if (target is None or not target.name or target.confidence < .9
                or not self.observer.chat.chat_ready()):
            return
        if not hasattr(self, "mana_chat_attempts"):
            self.mana_chat_attempts = deque(maxlen=16)
        self.mana_chat_attempts.append((sent_at, target.name, state.profile_revision,
                                        self.controller.pause_generation))

    def consume_mana_chat(self, state: HealerSnapshot) -> None:
        if (not isinstance(getattr(self, "own", None), BarProfile) or not self.own.system_chat_enabled):
            return
        generation = self.controller.pause_generation
        context = (generation, state.profile_revision)
        previous = getattr(self, "mana_chat_context", None)
        if previous != context:
            self.mana_chat_context = context
            self.mana_chat_attempts = deque(maxlen=16)
            self.observer.chat.reset_chat()
            return
        normalize = lambda text: " ".join(text.casefold().split()).rstrip(".! ")
        configured = {normalize(text) for text in self.config.mana_block_messages}
        now = time.monotonic()
        for event in self.observer.chat.take_chat_events():
            if not 0 <= now - event.at <= 3 or normalize(event.text) not in configured:
                continue
            attempts = [attempt for attempt in self.mana_chat_attempts
                        if 0 <= event.at - attempt[0] <= 3 and attempt[2:] == (state.profile_revision, generation)]
            # Несколько разных получателей в окне ответа не позволяют назначить виновника.
            names = {attempt[1] for attempt in attempts}
            if len(names) != 1:
                continue
            name = names.pop()
            if not any(m.name == name and m.confidence >= .9 for m in state.members):
                continue
            self.ee.mana_chat_blocked_names.add(self.ee.name_key(name))
            self.mana_chat_attempts.clear()
            self.report(f"ЕЕ: системный чат запрещает восстановление MP для {name}; "
                        f"заливка и Mana Gain исключены до конца сессии: {event.text}")

    def restore_timers(self) -> None:
        self.ee = EEPolicy(self.config)
        if not self.state_path.exists():
            return
        data = json.loads(self.state_path.read_text("utf-8"))
        if data["identity"] != self.identity:
            raise ValueError("Таймеры ЕЕ относятся к другому окну")
        now, wall = time.monotonic(), time.time()
        self.ee.buff_members = data.get("buff_members", {})
        quota = data.get("emergency_mana_used", [])
        if not isinstance(quota, list) or any(target not in {"@slot1", "@slot2"} for target in quota):
            raise ValueError("Некорректный список аварийных заливок ЕЕ")
        self.ee.emergency_mana_used = set(quota)
        for key, expiry in data["ready_at"].items():
            if key in {"area", "area_heal"}:
                key = "circlet_of_heal"
            remaining = float(expiry) - wall
            if not data.get("buff_schedule_version") and key.startswith(("buff:clarity:", "buff:shock:")):
                # Переводим старые 18-минутные таймеры на обновление через 10 минут.
                remaining -= 480
            if not math.isfinite(remaining) or remaining > 3600:
                raise ValueError("Некорректный таймер ЕЕ")
            self.ee.ready_at[key] = max(self.ee.ready_at.get(key, 0), now + max(0, remaining))
        remaining = float(data.get("busy_until", wall)) - wall
        if not math.isfinite(remaining) or remaining > 135:
            raise ValueError("Некорректный таймер каста ЕЕ")
        self.ee.busy_until = now + max(0, remaining)

    def save_timers(self) -> None:
        now, wall = time.monotonic(), time.time()
        data = {"identity": self.identity, "buff_schedule_version": 1,
                "buff_members": self.ee.buff_members,
                "emergency_mana_used": sorted(self.ee.emergency_mana_used),
                "ready_at": {k: wall + max(0, v - now) for k, v in self.ee.ready_at.items()},
                "busy_until": wall + max(0, self.ee.busy_until - now)}
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        temporary.replace(self.state_path)
        self.ee.mana_quota_dirty = False

    def execute_ee(self, decision: EEDecision, deadline: float) -> None:
        generation = self.controller.pause_generation
        state = self.latest()
        revision = state.profile_revision
        member = next((m for m in state.members if m.target_id == decision.target), None)

        def verify() -> None:
            current = self.latest()
            if (time.monotonic() >= deadline or generation != self.controller.pause_generation
                    or current.profile_revision != revision
                    or self.ee.decide(current, time.monotonic()) != decision):
                raise TargetChanged("Приоритет ЕЕ изменился во время наведения")
            if member is not None and member not in current.members:
                # Ресурсы могут меняться, но имя и строка должны оставаться прежними.
                if not any(m.slot == member.slot and m.name == member.name for m in current.members):
                    raise TargetChanged("Участник пати сменился")

        verify()
        if decision.target:
            x, y, slot = self.point(decision.target)
            x, y = self.click_point(x, y, slot)
            self.controller.click("left", x, y, verify=verify)
            self.observer.reader.identities.invalidate()
        verify()
        if decision.skill == "move":
            key = random.choice(("W", "A", "S", "D"))
            duration = random.randint(300, 3000 if self.controller.transport.protocol == "HID_PILOT_V4" else 1000)
            if time.monotonic() + duration / 1000 + .02 > deadline:
                return
            self.controller.key(key, duration)
            self.ee.next_move = time.monotonic() + random.uniform(30, 60)
            self.ee.next_follow = time.monotonic()
            self.report(f"ЕЕ: случайное движение {key}, {duration} мс")
            return
        if decision.skill == "follow":
            self.controller.key(self.config.follow_key, 100)
            self.ee.next_follow = time.monotonic() + self.config.follow_seconds
            return
        key, cast, cooldown = self.ee.timing(decision.skill)
        preparation = self.config.aura_cast if decision.skill == "bless" else 0
        if time.monotonic() + preparation + cast > deadline:
            return
        if decision.skill == "bless":
            self.controller.key("F8", 100)
            try:
                self.controller.wait(self.config.aura_cast)
                # После F8 завершаем последовательность, даже если обычный приоритет изменился.
                current = self.latest()
                if (generation != self.controller.pause_generation
                        or current.profile_revision != revision or current.hp is None or current.hp <= 0):
                    raise TargetChanged("Последовательность Bless Eva прервана паузой или потерей наблюдения")
            except TargetChanged:
                if generation == self.controller.pause_generation:
                    self.controller.key("F10", 100)
                raise
        sent_at = time.monotonic()
        if decision.skill in {"mana", "gain"}:
            current = self.latest()
            sent_at = time.monotonic()
            if self.ee.decide(current, sent_at) != decision:
                raise TargetChanged("Условия восстановления MP изменились перед нажатием")
            if decision.skill == "mana" and current.mp is not None and current.mp < .10:
                # Резервируем попытку после проверок, но до ввода и возможной потери ACK.
                self.ee.emergency_mana_used.add(decision.target)
                self.save_timers()
            self.record_mana_chat_attempt(decision, current, sent_at)
            self.record_mana_effect(decision, current, sent_at, cast)
        self.controller.key(key, 100)
        now = time.monotonic()
        self.ee.ready_at[decision.skill] = now + cooldown
        self.ee.busy_until = now + cast
        if decision.skill in {"clarity", "shock", "gain", "kiss"}:
            timer = self.ee.buff_timer(decision.skill, decision.target or "@self", state)
            interval = 600 if decision.skill in {"clarity", "shock"} else 1080
            self.ee.ready_at[timer] = now + cast + interval
        self.ee.next_follow = now + cast
        self.save_timers()
        self.report(f"ЕЕ: {decision.skill} {decision.target or ''}")
        self.controller.wait(cast)
        if decision.skill == "bless" and generation == self.controller.pause_generation:
            self.controller.key("F10", 100)

    def try_auto_loot(self, deadline: float) -> bool:
        now = time.monotonic()
        if not self.config.auto_loot or not self.loot.due(now):
            return False
        current = self.latest()
        now = time.monotonic()
        decision = self.ee.decide(current, now)
        safe = (decision is None and 0 <= now - current.at <= 1
                and now >= self.ee.busy_until and current.roster_known
                and current.hp is not None and current.hp >= self.config.self_heal_hp_threshold
                and current.mp is not None and current.mp >= .05
                and all(m.hp is not None and m.hp >= self.config.heal_hp_threshold
                        and m.mp is not None for m in current.members))
        if not safe or now + .12 > deadline:
            self.loot.interrupt(now)
            return False
        self.controller.key(self.config.loot_key, 100)
        self.loot.committed(time.monotonic())
        logger.debug("ЕЕ автолут: %s; в серии осталось %s", self.config.loot_key, self.loot.remaining)
        return True

    def run(self, deadline: float | None = None) -> None:
        end = min(time.monotonic() + self.config.max_seconds,
                  deadline if deadline is not None else float("inf"))
        self.observer.start()
        generation = self.controller.pause_generation
        try:
            while time.monotonic() < end:
                self.controller.checkpoint()
                if generation != self.controller.pause_generation:
                    generation = self.controller.pause_generation
                    self.ee.reset_damage(after=time.monotonic())
                    self.ee.next_move = time.monotonic() + random.uniform(30, 60)
                    self.loot = AutoLoot(time.monotonic())
                try:
                    state = self.latest()
                    if state.hp == 0:
                        self.await_resurrection(end)
                        self.ee.reset_damage(after=time.monotonic())
                        self.ee.next_move = time.monotonic() + random.uniform(30, 60)
                        self.controller.wait(self.config.poll_seconds)
                        continue
                    decision = self.ee.decide(state, time.monotonic())
                    if self.ee.mana_quota_dirty:
                        self.save_timers()
                    publish = getattr(self.notify, "state", None)
                    if publish:
                        reason = decision.skill if decision else "ожидание"
                        if state.mp is not None and state.mp < .05:
                            reason += "; MP <5%: восстановление маны / хил себя / следование"
                        publish({"state": state, "reason": f"ЕЕ: {reason}",
                                 "rates": dict(self.ee.damage_rates),
                                 "buffs": self.ee.buff_status(state, time.monotonic())})
                    if decision:
                        self.loot.interrupt(time.monotonic())
                        self.execute_ee(decision, end)
                    else:
                        self.try_auto_loot(end)
                except TargetChanged as exc:
                    self.ee.reset_damage(after=time.monotonic())
                    logger.debug("Действие ЕЕ отложено: %s", exc)
                wait = self.config.poll_seconds
                if self.config.auto_loot and self.loot.remaining:
                    wait = min(wait, max(.01, self.loot.next_at - time.monotonic()))
                self.controller.wait(wait)
        finally:
            self.observer.close()


def run_extension(controller: ActionController, base: Path, args: tuple[str, ...],
                  config: EEConfig, notify: Callable[[str], None], deadline: float) -> None:
    own = BarProfile.model_validate_json((base / args[0]).read_text("utf-8"))
    party = PartyProfile.model_validate_json((base / args[1]).read_text("utf-8"))
    EEFarmer(controller, own, party, config, notify, base / "ee-cooldowns.json").run(deadline)
