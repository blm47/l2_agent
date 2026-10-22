"""
Хилер париков: сбор без агро на хилера, спасение танка и бой группы.
"""

from enum import StrEnum

from pydantic import Field, model_validator

from hid_pilot.healer import HealDecision, HealerConfig, HealerSnapshot, HealPolicy


class TrainConfig(HealerConfig):
    circlet_of_heal_enabled: bool = True
    auto_loot: bool = False
    follow_key: str | None = "9"
    follow_seconds: float = Field(default=2.5, ge=1, le=300)
    mp_buff_key: str | None = "F11"
    invocation_mp: float = Field(default=.8, gt=0, le=1)
    tank_slot: int = Field(default=0, ge=0, le=8)
    collect_poll_seconds: float = Field(default=.1, ge=.1, le=.1)
    combat_poll_seconds: float = Field(default=.2, ge=.2, le=.3)
    collect_min_damage: float = Field(default=.005, gt=0, le=.05)
    collect_max_damage: float = Field(default=.05, gt=0, le=.1)
    arrival_damage: float = Field(default=.1, gt=.05, le=.5)
    emergency_hp: float = Field(default=.16, gt=0, le=.2)
    collect_heal_hp: float = Field(default=.3, gt=.16, le=.5)
    maintain_hp: float = Field(default=.7, gt=.5, lt=1)
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
        if self.emergency_hp >= self.collect_heal_hp:
            raise ValueError("Аварийный баланс должен быть ниже порога лечения сборщика")
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
        self.collectors: set[str] = set()
        self.burst_targets: set[str] = set()
        self.full_heal_until = float("-inf")
        self.mass_check: tuple[dict[str, float], float, float, int] | None = None
        self.mass_helped: set[str] = set()
        self.reachable_until: dict[str, float] = {}
        self.overwhelmed_until: dict[str, float] = {}
        self.mass_opened = False
        self.heavy_damage_until: dict[str, float] = {}
        self.mass_cast_finished_at: float | None = None

    @property
    def tank_target(self) -> str | None:
        return f"@slot{self.tank_slot}" if self.tank_slot is not None else None

    def ingest(self, state: HealerSnapshot) -> None:
        previous = self.previous
        if previous is not None and previous.profile_revision != state.profile_revision:
            previous = None
            self.collectors.clear()
            self.mass_check = None
            self.reachable_until.clear()
            self.overwhelmed_until.clear()
            self.mass_opened = False
            self.heavy_damage_until.clear()
            self.mass_cast_finished_at = None
            if self.config.tank_slot == 0:
                self.tank_slot = None
        if previous is not None and state.at <= previous.at:
            return
        if previous is not None and state.at - previous.at > .75:
            self.mass_check = None
            self.mass_helped.clear()
            self.reachable_until.clear()
            self.overwhelmed_until.clear()
            self.heavy_damage_until.clear()
            self.mass_cast_finished_at = None
        self.observe_mass_effect(state)
        # В париках отсутствие эффекта не должно запускать восстановительное следование.
        self.heal_attempt_since = None
        self.heal_attempt_targets.clear()
        self.recovery_follow = False
        pending = self.pending
        self.burst_targets = set()
        super().ingest(state)
        state = self.monitored_state(state)
        health = state.health()
        self.heavy_damage_until = {target: until for target, until in self.heavy_damage_until.items()
                                   if target in health and 0 < health[target] < 1 and state.at <= until}
        for target, hp in health.items():
            history = self.history.get(target)
            after_mass = (self.mass_cast_finished_at is not None and history is not None
                          and len(history) >= 3
                          and list(history)[-3][0] >= self.mass_cast_finished_at)
            if after_mass and 0 < hp < 1 and self.strong_damage(target):
                # Не теряем срочную цель из-за одного кадра восстановления HP при наведении.
                self.heavy_damage_until[target] = state.at + self.config.fight_quiet_seconds
        if (state.health() and min(state.health().values()) >= self.config.maintain_hp
                and state.at - self.last_damage >= self.config.fight_quiet_seconds):
            self.mass_opened = False
            self.mass_cast_finished_at = None
        if self.tank_slot in self.ignored_slots:
            self.tank_slot = max((member.slot for member in state.members), default=None)
        if (not self.chain_heals and pending is not None and pending[0].skill == "purify" and state.at >= pending[2]
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
        self.burst_targets = {target for target, hp in health.items() if old[target] - hp > .15 + 1e-9}
        strong_count = sum(self.strong_damage(target) and not self.destroyer_protected(state, target) for target in health)
        if strong_count:
            self.full_heal_until = state.at + cfg.fight_quiet_seconds
        if len(self.burst_targets) >= 3:
            self.phase = TrainPhase.COMBAT
            self.last_party_hit = state.at
            cfg.poll_seconds = cfg.combat_poll_seconds
            return
        candidates = self.collectors | (hits - {"@self"})
        outsiders_healthy = all(hp >= cfg.healthy_hp for target, hp in health.items() if target not in candidates)
        collecting = (bool(candidates) and len(candidates) <= 2 and "@self" not in hits
                      and outsiders_healthy)
        strong = any(self.strong_damage(target, cfg.arrival_damage) for target in candidates)
        if hits:
            self.last_tank_hit = state.at
        elif (min(health.values()) >= cfg.healthy_hp
              and state.at - max(self.last_party_hit, self.last_tank_hit) >= cfg.fight_quiet_seconds):
            self.phase = TrainPhase.IDLE
            self.collectors.clear()
            self.arrival_pending = False
            return
        if collecting and (self.phase != TrainPhase.COMBAT or state.at - self.last_party_hit >= cfg.fight_quiet_seconds):
            self.collectors = candidates
            self.phase = TrainPhase.COLLECT
            self.arrival_pending = False
            return
        others_hit = hits - self.collectors if self.collectors else hits - {tank}
        if tank in hits:
            self.last_tank_hit = state.at
        if others_hit:
            self.last_party_hit = state.at
            self.phase = TrainPhase.COMBAT
            self.collect_since = None
        elif self.phase != TrainPhase.COMBAT and strong:
            self.phase = TrainPhase.COMBAT
            self.arrival_pending = True
            self.last_party_hit = state.at
            self.collect_since = None
        elif self.phase == TrainPhase.COMBAT:
            other_healthy = all(hp >= cfg.healthy_hp for target, hp in health.items() if target != tank)
            if (state.at - self.last_party_hit >= cfg.fight_quiet_seconds and other_healthy
                    and tank in hits and cfg.collect_min_damage - 1e-9 <= self.rate(tank) <= cfg.collect_max_damage + 1e-9):
                self.phase = TrainPhase.COLLECT
                self.arrival_pending = False
            elif state.at - max(self.last_party_hit, self.last_tank_hit) >= cfg.fight_quiet_seconds:
                self.phase = TrainPhase.IDLE
                self.collectors.clear()
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
                       and not (blocked_tank and (f"@slot{slot}" in self.collectors or slot == self.tank_slot and not self.collectors)))
        if slots:
            return self.cast("purify", "Снять паралич по порядку пати", f"@slot{slots[0]}")
        return None

    def heal_goal(self, state: HealerSnapshot) -> float:
        return 1. if state.at <= self.full_heal_until else self.config.maintain_hp

    def needs_single(self, state: HealerSnapshot, target: str) -> bool:
        hp = state.health().get(target)
        return (not self.destroyer_protected(state, target) and hp is not None
                and (target == "@self" or self.urgent_single(state, target))
                and (hp < self.heal_goal(state) or self.strong_damage(target)))

    def urgent_single(self, state: HealerSnapshot, target: str) -> bool:
        hp = state.health().get(target)
        return (hp is not None and 0 < hp < 1
                and not self.destroyer_protected(state, target)
                and state.at <= self.heavy_damage_until.get(target, float("-inf")))

    def needs_battle(self, state: HealerSnapshot, target: str) -> bool:
        hp = state.health().get(target)
        return hp is not None and hp < self.config.single_hp and self.urgent_single(state, target)

    def urgent_targeted_heal(self, state: TrainSnapshot, now: float) -> HealDecision | None:
        targets = [target for target in state.health() if self.urgent_single(state, target)]
        if not targets:
            return None
        target = min(targets, key=state.health().get)
        for skill in ("battle", "major"):
            if skill == "battle" and not self.needs_battle(state, target):
                continue
            if (skill != "major" or self.config.allow_ore) and self.ready(skill, now):
                return self.cast(skill, "После массового хила HP продолжает сильно падать: выделить и лечить точечно", target)
        return None

    def observe_mass_effect(self, state: HealerSnapshot) -> None:
        if self.mass_check is None:
            return
        before, started, until, revision = self.mass_check
        if state.profile_revision != revision or state.at > until + .75:
            self.mass_check = None
            self.mass_helped.clear()
            return
        if state.at <= started:
            return
        for target, hp in state.health().items():
            if target in before and hp >= before[target] + .015:
                self.mass_helped.add(target)
        if state.at >= until:
            # Прирост HP — разрешение короткой попытки, а не доказательство дистанции.
            if self.mass_helped:
                self.stun_suspected = False
            for target in before:
                hp = state.health().get(target)
                if hp is not None and hp < self.config.danger_hp:
                    self.overwhelmed_until[target] = state.at + self.config.fight_quiet_seconds
                else:
                    self.overwhelmed_until.pop(target, None)
                if target in self.mass_helped:
                    self.reachable_until[target] = state.at + self.config.fight_quiet_seconds
                else:
                    self.reachable_until.pop(target, None)
            self.mass_check = None
            self.mass_helped.clear()

    def rescue_tank(self, state: TrainSnapshot, now: float, target: str | None = None) -> HealDecision:
        target = target or self.tank_target
        if state.roster_known and self.ready("balance", now):
            return self.cast("balance", "Спасение танка: баланс в приоритете")
        if not self.needs_single(state, target):
            for skill in ("group", "mass_major"):
                if (skill != "mass_major" or self.config.allow_ore) and self.ready(skill, now):
                    return self.cast(skill, "Спасение с места без подтверждённой доступности цели")
        skills = ("battle", "major") if self.mass_opened or self.phase == TrainPhase.COMBAT or self.urgent_single(state, target) else ("battle",)
        for skill in skills:
            if not self.needs_single(state, target):
                continue
            if skill == "battle" and not self.needs_battle(state, target):
                continue
            if (skill != "major" or self.config.allow_ore) and self.ready(skill, now):
                return self.cast(skill, "Спасение участника: чередовать готовые хилы", target)
        return HealDecision(kind="wait", reason="Спасение танка: ожидание готового одиночного хила")

    def follow_or_wait(self, state: TrainSnapshot, now: float, reason: str) -> HealDecision:
        follow = self.emergency_follow(state, now)
        if follow is not None:
            return follow.model_copy(update={"reason": "Подойти к ведущему вне сбора парика"})
        return HealDecision(kind="wait", reason=reason)

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
        health = state.health()
        tank = self.tank_target
        tank_hp = health.get(tank)
        if tank_hp is None or not state.roster_known:
            if state.mp >= self.config.minimum_cast_mp:
                targeted = self.urgent_targeted_heal(state, now)
                if targeted is not None:
                    return targeted
            return self.follow_or_wait(state, now, "Неизвестны танк или состав пати")
        blocked = self.phase in {TrainPhase.UNKNOWN, TrainPhase.COLLECT}
        if not blocked:
            rescue = self.self_rescue_decision(state, now)
            if rescue is not None and rescue.kind != "probe":
                return rescue
        if not blocked:
            emergency = self.emergency_balance(state, now)
            if emergency is not None:
                return emergency
        # Не переключаем всю пати на восстановление по одному отсутствующему эффекту.
        if state.mp < self.config.minimum_cast_mp:
            return self.follow_or_wait(state, now, "MP ниже резерва")
        healable = {target: hp for target, hp in health.items() if not self.destroyer_protected(state, target)}
        lowest = min(healable, key=healable.get)
        if self.mass_opened:
            # Первый масс-хил не должен вытеснять спасение готовыми скиллами во всех фазах.
            if self.ready("balance", now) and health[lowest] <= self.config.critical_hp:
                return self.cast("balance", "После первого масс-хила: выровнять HP и спасти проседающую цель")
        if (self.circlet_of_heal_needed(state)
                and now < self.ready_at.get("balance", 0) and self.ready("circlet_of_heal", now)):
            return self.cast("circlet_of_heal", "Низкое HP: хил вокруг себя Пробелом, баланс в откате")
        if health[lowest] <= self.config.emergency_hp + 1e-9:
            return self.rescue_tank(state, now, lowest)
        targeted = self.urgent_targeted_heal(state, now)
        if targeted is not None:
            return targeted
        if self.mass_opened:
            if self.needs_balance(health) and self.ready("balance", now):
                return self.cast("balance", "После первого масс-хила: выровнять разницу HP")
            injured = [target for target, hp in healable.items()
                       if hp < self.config.danger_hp and self.needs_single(state, target)]
            if injured:
                target = min(injured, key=health.get)
                for skill in ("battle", "major"):
                    if skill == "battle" and not self.needs_battle(state, target):
                        continue
                    if (skill != "major" or self.config.allow_ore) and self.ready(skill, now):
                        return self.cast(skill, "После масс-хила: срочное одиночное лечение", target)
            if (sum(hp < self.config.danger_hp for hp in healable.values()) >= 2
                    and self.ready("avatar", now)):
                return self.cast("avatar", "После масс-хила: несколько участников остаются в опасности")
            if (self.config.allow_ore and self.ready("mass_major", now)
                    and any(hp < self.config.danger_hp or self.strong_damage(target)
                            for target, hp in healable.items())):
                return self.cast("mass_major", "После первого масс-хила: усилить лечение массовым мажором")
        if self.mass_check is not None:
            return HealDecision(kind="wait", reason="Ожидание эффекта массового хила")
        if len(self.burst_targets) >= 3:
            if self.ready("group", now):
                return self.cast("group", "За тик 3+ участника потеряли больше 15 пунктов HP: обычный масс-хил первым")
            if self.config.allow_ore and self.ready("mass_major", now):
                return self.cast("mass_major", "За тик 3+ участника потеряли больше 15 пунктов HP")
            if self.ready("group", now):
                return self.cast("group", "Резкий массовый урон: мажор групп хил недоступен")
        clean = self.purify(state, now, blocked)
        if clean is not None:
            return clean
        if (now >= self.support_ready_at and self.urgent_invocation(state, now)
                and self.ready("invocation", now)):
            return self.cast("invocation", "MP ниже порога; по всей пати нет урона 30 секунд")
        if blocked:
            # Массовый хил проверяет доступность без выделения и погони за сборщиком.
            if any(hp < self.heal_goal(state) for hp in healable.values()):
                for skill in ("group", "mass_major"):
                    if (skill != "mass_major" or self.config.allow_ore) and self.ready(skill, now):
                        return self.cast(skill, "Массовый хил с места во время сбора")
            injured = [target for target in health if self.needs_single(state, target)]
            if injured:
                target = min(injured, key=health.get)
                for skill in ("battle", "major"):
                    if skill == "battle" and not self.needs_battle(state, target):
                        continue
                    if (skill != "major" or self.config.allow_ore) and self.ready(skill, now):
                        return self.cast(skill, "Одиночный хил после наблюдаемого эффекта массового", target)
            buff = self.mp_buff_decision(now)
            if self.phase == TrainPhase.COLLECT and buff is not None:
                return buff
            return self.follow_or_wait(state, now, "Сбор: оставаться на месте, не преследовать сборщиков")
        if self.phase == TrainPhase.COMBAT:
            # Сближение к отдельной цели в париках не включаем.
            self.chase_name = None
        if (state.mp >= self.config.minimum_cast_mp and self.config.allow_ore and self.health_complete(state)
                and any(hp < self.heal_goal(state) for hp in healable.values())):
            if self.mass_opened and self.needs_balance(health) and self.ready("balance", now):
                return self.cast("balance", "Разница HP: баланс перед мажор групп хилом")
            if self.ready("group", now):
                return self.cast("group", "Основной хил париков: обычный масс-хил первым")
            if self.ready("mass_major", now):
                return self.cast("mass_major", f"Основной хил париков: поддерживать HP до {self.heal_goal(state):.0%}")
        if any(hp < self.heal_goal(state) for hp in healable.values()) and self.ready("group", now):
            return self.cast("group", "Массовый хил с места: основной недоступен")
        decision = super().decide(state, now)
        if decision.kind in {"follow", "chase"}:
            return self.follow_or_wait(state, now, "Без подтверждённого эффекта не сближаемся")
        if decision.kind == "probe":
            return HealDecision(kind="wait", reason="Нет эффекта: ожидание следующего массового хила")
        return decision

    def committed(self, decision: HealDecision, state: HealerSnapshot, now: float) -> None:
        super().committed(decision, state, now)
        if decision.kind == "cast" and decision.skill in {"group", "mass_major", "avatar"}:
            self.mass_opened = True
            self.mass_cast_finished_at = now + self.config.cast_seconds(decision.skill)
            self.heavy_damage_until.clear()
            before = {target: hp for target, hp in state.health().items() if hp < self.heal_goal(state)}
            self.mass_check = (before, now, now + self.config.cast_seconds(decision.skill)
                               + self.config.verify_seconds, state.profile_revision)
            self.mass_helped.clear()
            for target in before:
                self.reachable_until.pop(target, None)
                self.overwhelmed_until.pop(target, None)
        if decision.skill == "balance" or (decision.target == self.tank_target and decision.skill in {"battle", "major"}):
            self.arrival_pending = False
