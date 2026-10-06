"""
Хилер париков: сбор без агро на хилера, спасение танка и бой группы.
"""

from enum import StrEnum

from pydantic import Field, model_validator

from hid_pilot.healer import HealDecision, HealerConfig, HealerSnapshot, HealPolicy


class TrainConfig(HealerConfig):
    follow_key: str | None = None
    follow_seconds: float = Field(default=7, ge=1, le=300)
    mp_buff_key: str | None = "F11"
    invocation_mp: float = Field(default=.2, gt=0, le=1)
    tank_slot: int = Field(default=0, ge=0, le=8)
    collect_poll_seconds: float = Field(default=.1, ge=.1, le=.1)
    combat_poll_seconds: float = Field(default=.2, ge=.2, le=.3)
    collect_min_damage: float = Field(default=.005, gt=0, le=.05)
    collect_max_damage: float = Field(default=.05, gt=0, le=.1)
    arrival_damage: float = Field(default=.1, gt=.05, le=.5)
    emergency_hp: float = Field(default=.1, gt=0, le=.2)
    healthy_hp: float = Field(default=.9, ge=.8, le=1)
    party_damage_epsilon: float = Field(default=.002, gt=0, le=.02)
    fight_quiet_seconds: float = Field(default=3, ge=1, le=10)
    stun_probe_during_collect: bool = False

    @model_validator(mode="after")
    def collect_thresholds(self) -> "TrainConfig":
        if self.collect_min_damage >= self.collect_max_damage:
            raise ValueError("Нужен collect_min_damage < collect_max_damage")
        if self.arrival_damage <= self.collect_max_damage:
            raise ValueError("Порог возвращения должен превышать урон при сборе")
        return self


class TrainPhase(StrEnum):
    UNKNOWN = "UNKNOWN"
    IDLE = "IDLE"
    COLLECT = "COLLECT"
    COMBAT = "COMBAT"


class TrainSnapshot(HealerSnapshot):
    paralyzed_slots: tuple[int, ...] = ()
    debuffs_known: bool = False


class TrainPolicy(HealPolicy):
    def __init__(self, config: TrainConfig, now: float) -> None:
        super().__init__(config, now)
        self.phase = TrainPhase.UNKNOWN
        self.tank_slot = config.tank_slot or None
        self.collect_since: float | None = None
        self.last_party_hit = now
        self.last_tank_hit = now
        self.arrival_pending = False
        self.paralyzed_slots: tuple[int, ...] = ()

    @property
    def tank_target(self) -> str | None:
        return f"@slot{self.tank_slot}" if self.tank_slot is not None else None

    def ingest(self, state: HealerSnapshot) -> None:
        previous = self.previous
        if previous is not None and state.at <= previous.at:
            return
        pending = self.pending
        super().ingest(state)
        state = self.monitored_state(state)
        if self.tank_slot in self.ignored_slots:
            self.tank_slot = max((member.slot for member in state.members), default=None)
        if (pending is not None and pending[0].skill == "purify" and state.at >= pending[2]
                and isinstance(state, TrainSnapshot) and state.debuffs_known
                and pending[0].target in {f"@slot{slot}" for slot in state.paralyzed_slots}):
            self.stun_suspected = True
            self.feedback_note = "Пурифай не снял паралич: возможен стан на хилере или дальность; дебафф хилера не подтверждён"
        self.paralyzed_slots = tuple(slot for slot in state.paralyzed_slots if slot not in self.ignored_slots
                                    ) if isinstance(state, TrainSnapshot) and state.debuffs_known else ()
        cfg = self.config
        cfg.poll_seconds = cfg.collect_poll_seconds
        if self.tank_slot is None and state.roster_known and state.members:
            self.tank_slot = max(member.slot for member in state.members)
        health = state.health()
        tank = self.tank_target
        if (not state.roster_known or state.hp is None or state.mp is None
                or tank not in health or any(member.hp is None for member in state.members)):
            self.phase = TrainPhase.UNKNOWN
            self.collect_since = None
            return
        if previous is None or state.at - previous.at > .75:
            # После старта/пропуска по одному кадру разрешаем только спокойное состояние.
            self.phase = TrainPhase.IDLE if min(health.values()) >= cfg.healthy_hp else TrainPhase.UNKNOWN
            self.collect_since = None
            return
        old = previous.health()
        if set(old) != set(health):
            self.phase = TrainPhase.UNKNOWN
            self.collect_since = None
            return
        hits = {target for target, hp in health.items()
                if old[target] - hp > max(.0002, cfg.party_damage_epsilon * (state.at - previous.at))}
        others_hit = hits - {tank}
        if tank in hits:
            self.last_tank_hit = state.at
        if others_hit:
            self.last_party_hit = state.at
            self.phase = TrainPhase.COMBAT
            self.collect_since = None
        elif self.phase != TrainPhase.COMBAT and self.strong_damage(tank, cfg.arrival_damage):
            self.phase = TrainPhase.COMBAT
            self.arrival_pending = True
            self.last_party_hit = state.at
            self.collect_since = None
        elif self.phase == TrainPhase.COMBAT:
            other_healthy = all(hp >= cfg.healthy_hp for target, hp in health.items() if target != tank)
            if (state.at - self.last_party_hit >= cfg.fight_quiet_seconds and other_healthy
                    and tank in hits and cfg.collect_min_damage - 1e-9 <= self.rate(tank) <= cfg.collect_max_damage + 1e-9):
                self.phase = TrainPhase.COLLECT
                self.follow_needed = False
                self.arrival_pending = False
            elif state.at - max(self.last_party_hit, self.last_tank_hit) >= cfg.fight_quiet_seconds:
                self.phase = TrainPhase.IDLE
                self.follow_needed = True
                self.arrival_pending = False
        elif tank in hits and self.phase != TrainPhase.COLLECT:
            other_healthy = all(hp >= cfg.healthy_hp for target, hp in health.items() if target != tank)
            damage = self.rate(tank)
            if other_healthy and cfg.collect_min_damage - 1e-9 <= damage <= cfg.collect_max_damage + 1e-9:
                if self.collect_since is None:
                    self.collect_since = state.at
                # Уже при первой медленной потере запрещаем хил: подтверждение не открывает окно агро.
                self.phase = TrainPhase.COLLECT
                self.follow_needed = False
            else:
                # Урон только по танку без доказанного возвращения не разрешает обычный хил.
                self.phase = TrainPhase.UNKNOWN
        elif self.phase == TrainPhase.UNKNOWN and min(health.values()) >= cfg.healthy_hp:
            self.phase = TrainPhase.IDLE
        cfg.poll_seconds = cfg.combat_poll_seconds if self.phase == TrainPhase.COMBAT else cfg.collect_poll_seconds

    def purify(self, state: TrainSnapshot, now: float, blocked_tank: bool) -> HealDecision | None:
        if not state.debuffs_known or not self.ready("purify", now):
            return None
        valid = {member.slot for member in state.members if member.hp is not None and member.mp is not None}
        slots = sorted(slot for slot in state.paralyzed_slots if slot in valid
                       and not (blocked_tank and slot == self.tank_slot))
        if slots:
            return self.cast("purify", "Снять паралич по порядку пати", f"@slot{slots[0]}")
        return None

    def rescue_tank(self, state: TrainSnapshot, now: float) -> HealDecision:
        if state.roster_known and self.ready("balance", now):
            return self.cast("balance", "Спасение танка: баланс в приоритете")
        skills = ("battle", "major") if self.phase == TrainPhase.COMBAT else ("battle",)
        for skill in skills:
            if skill == "battle" and not self.needs_battle(state, self.tank_target):
                continue
            if (skill != "major" or self.config.allow_ore) and self.ready(skill, now):
                return self.cast(skill, "Спасение танка: чередовать готовые хилы", self.tank_target)
        return HealDecision(kind="wait", reason="Спасение танка: ожидание готового одиночного хила")

    def decide(self, state: TrainSnapshot, now: float) -> HealDecision:
        self.ingest(state)
        state = self.monitored_state(state)
        if 0 <= now - state.at <= .5 and state.hp == 0:
            return HealDecision(kind="stop", reason="Собственное HP = 0")
        if now < self.busy_until:
            return HealDecision(kind="wait", reason="Каст / Invocation")
        if now - state.at > .5 or state.at > now or state.hp is None or state.mp is None:
            return HealDecision(kind="wait", reason="Нет свежих HP/MP для парика")
        if state.hp == 0:
            return HealDecision(kind="stop", reason="Собственное HP = 0")
        if self.emergency_slots(now) and self.phase in {TrainPhase.UNKNOWN, TrainPhase.COLLECT}:
            follow = self.emergency_follow(state, now)
            if follow is not None:
                return follow
        health = state.health()
        tank = self.tank_target
        tank_hp = health.get(tank)
        if tank_hp is None or not state.roster_known:
            return HealDecision(kind="wait", reason="Неизвестны танк или состав пати")
        blocked = self.phase in {TrainPhase.UNKNOWN, TrainPhase.COLLECT}
        if not blocked:
            emergency = self.emergency_balance(state, now)
            if emergency is not None:
                return emergency
        if self.stun_suspected:
            if blocked and not self.config.stun_probe_during_collect:
                self.probe_started_at = None
                self.probe_frame_at = None
                self.probe_baseline.clear()
                return HealDecision(kind="wait", reason="Подозрение на стан: Пробел запрещён при сборе парика")
            return self.stun_probe_decision(state, now)
        if state.mp < self.config.minimum_cast_mp:
            return HealDecision(kind="wait", reason="MP ниже резерва")
        if tank_hp < self.config.emergency_hp or (self.arrival_pending and tank_hp < self.config.single_hp):
            return self.rescue_tank(state, now)
        clean = self.purify(state, now, blocked)
        if clean is not None:
            return clean
        if blocked:
            # Любые массовые эффекты и целевые действия на танка запрещены во время сбора.
            injured = [target for target in health if target != tank and self.needs_single(state, target)]
            if injured:
                target = min(injured, key=health.get)
                for skill in ("battle", "major"):
                    if skill == "battle" and not self.needs_battle(state, target):
                        continue
                    if (skill != "major" or self.config.allow_ore) and self.ready(skill, now):
                        return self.cast(skill, "Соло лечение без агро танка при сборе", target)
            buff = self.mp_buff_decision(now)
            if self.phase == TrainPhase.COLLECT and buff is not None:
                return buff
            return HealDecision(kind="wait", reason=f"{self.phase}: не лечить и не пурифаить танка")
        if self.phase == TrainPhase.COMBAT:
            # В бою следование и сближение не должны вытеснять хил и пурифай.
            self.follow_needed = False
            self.next_follow_at = now + self.config.follow_seconds
            self.chase_name = None
        return super().decide(state, now)

    def committed(self, decision: HealDecision, state: HealerSnapshot, now: float) -> None:
        super().committed(decision, state, now)
        if decision.skill == "balance" or (decision.target == self.tank_target and decision.skill in {"battle", "major"}):
            self.arrival_pending = False
