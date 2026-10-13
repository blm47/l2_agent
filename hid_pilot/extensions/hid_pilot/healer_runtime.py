"""
Исполнение решений хилера через ActionController и сохранение таймеров скиллов.
"""

import json
import logging
import math
import random
import time
from collections.abc import Callable
from pathlib import Path

from hid_pilot.auto_loot import AutoLoot
from hid_pilot.bars import BarProfile
from hid_pilot.controller import ActionController
from hid_pilot.healer import HealDecision, HealerConfig, HealerSnapshot, HealPolicy
from hid_pilot.healer_log import format_state
from hid_pilot.healer_observer import HealerObserver, HealerReader
from hid_pilot.party import PartyProfile
from hid_pilot.skill_aura import SkillAura

logger = logging.getLogger("hid_pilot.healer")


def prepare_extension(base: Path, args: tuple[str, ...]) -> None:
    own = BarProfile.model_validate_json((base / args[0]).read_text("utf-8"))
    party = PartyProfile.model_validate_json((base / args[1]).read_text("utf-8"))
    HealerReader(own, party)


def run_extension(controller: ActionController, base: Path, args: tuple[str, ...],
                  config: HealerConfig, notify: Callable[[str], None], deadline: float) -> None:
    own = BarProfile.model_validate_json((base / args[0]).read_text("utf-8"))
    party = PartyProfile.model_validate_json((base / args[1]).read_text("utf-8"))
    Healer(controller, own, party, config, notify, base / "healer-cooldowns.json").run(deadline)


class TargetChanged(Exception):
    pass


class Healer:
    def __init__(self, controller: ActionController, own: BarProfile, party: PartyProfile,
                 config: HealerConfig, notify: Callable[[str], None], state_path: Path) -> None:
        if controller.transport.protocol not in {"HID_PILOT_V2", "HID_PILOT_V3"}:
            raise ValueError("Хилер требует HID_PILOT_V2 (NUM7 и -); игровой ввод не начат")
        self.controller = controller
        self.own = own
        self.party = party
        self.config = config
        self.notify = notify
        self.state_path = state_path
        self.policy = HealPolicy(config, time.monotonic())
        self.policy.humanize = config.humanize
        self.policy.chain_heals = config.chain_heals
        if config.humanize:
            self.policy.support_ready_at += random.uniform(0, 5)
        self.controller.humanize = config.humanize
        self.loot = AutoLoot(time.monotonic())
        self.approach_until: float | None = None
        self.active_cast: tuple[HealDecision, float, bool] | None = None
        self.cast_revision = 0
        self.aura = SkillAura(controller, config.aura_key, notify, config.aura_cast)
        self.observer = HealerObserver(controller.desktop, HealerReader(own, party),
                                       config.poll_seconds, controller.paused.is_set)
        self.identity = controller.desktop.target.title
        self.restore_timers()

    def report(self, message: str) -> None:
        logger.info(message)
        self.notify(message)

    def restore_timers(self) -> None:
        if not self.state_path.exists():
            return
        data = json.loads(self.state_path.read_text("utf-8"))
        if data.get("identity") != self.identity:
            raise ValueError("Файл откатов относится к другому персонажу; нужен отдельный сценарий/папка")
        now, wall = time.monotonic(), time.time()
        for skill, expiry in data["ready_at"].items():
            if skill not in {"balance", "battle", "major", "mass_major", "group", "avatar", "invocation", "recharge", "purify", "mp_buff", "mp_buff2", "self_rescue", "area_heal"}:
                raise ValueError("Неизвестный скилл в файле откатов")
            remaining = float(expiry) - wall
            limit = 3605 if skill in {"mp_buff", "mp_buff2"} else 3600
            if not -86400 * 365 <= remaining <= limit:
                raise ValueError("Некорректный таймер отката")
            self.policy.ready_at[skill] = now + max(0, remaining)
        remaining = float(data.get("busy_until", 0)) - wall
        if not math.isfinite(remaining) or remaining > 135:
            raise ValueError("Некорректный таймер каста")
        self.policy.busy_until = now + max(0, remaining)

    def save_timers(self) -> None:
        now, wall = time.monotonic(), time.time()
        data = {"identity": self.identity,
                "ready_at": {key: wall + max(0, value - now) for key, value in self.policy.ready_at.items()},
                "busy_until": wall + max(0, self.policy.busy_until - now)}
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2), "utf-8")
        temporary.replace(self.state_path)

    def latest(self) -> HealerSnapshot:
        states = self.observer.snapshots()
        if not states or time.monotonic() - states[-1].at > 1:
            raise TargetChanged("Нет свежего кадра для действия")
        reader = self.observer.reader
        if isinstance(reader, HealerReader):
            with reader.profile_lock:
                if states[-1].profile_revision != reader.revision:
                    raise TargetChanged("Фоновая разметка изменилась; ожидается свежий кадр")
                self.own, self.party = reader.own, reader.party
        return self.policy.monitored_state(states[-1])

    def cancel_cast_if_unneeded(self, state: HealerSnapshot, now: float) -> bool:
        if not self.config.cancel_unneeded_casts or self.active_cast is None:
            return False
        decision, until, ambiguous = self.active_cast
        if state.profile_revision != self.cast_revision:
            self.active_cast = None
            return False
        if now >= until:
            self.active_cast = None
            return False
        if not 0 <= now - state.at <= .5 or state.hp is None or state.hp <= 0 or state.mp is None or not state.roster_known:
            return False
        if any(member.hp is None for member in state.members):
            return False
        if not self.policy.health_complete(state):
            return False
        if ambiguous:
            needed = any(self.policy.needs_single(state, target) for target in state.health())
        elif decision.skill == "purify":
            if not getattr(state, "debuffs_known", False):
                return False
            needed = decision.target in {f"@slot{slot}" for slot in state.paralyzed_slots}
        elif decision.target:
            needed = self.policy.needs_single(state, decision.target)
        else:
            needed = any(self.policy.needs_single(state, target) for target in state.health())
            if decision.skill == "balance":
                needed = needed or self.policy.needs_balance(state.health())
        if needed:
            return False
        self.controller.key("ESC", 100)
        if self.config.aura_enabled:
            self.aura.after(now, replace=True)
            self.aura.tick(time.monotonic())
        self.active_cast = None
        self.policy.busy_until = now
        self.policy.pending = None
        self.save_timers()
        self.report(f"ESC: отмена расчётного каста {decision.skill}; лечение больше не требуется; HP={state.health()}")
        return True

    def point(self, name: str) -> tuple[float, float, int]:
        state = self.latest()
        if name == "@self":
            box = self.own.numbers.get("hp") or self.own.bars.get("hp")
            return (box.x1 + box.x2) / 2, (box.y1 + box.y2) / 2, 0
        members = [member for member in state.members if member.target_id == name and (
            member.hp is not None or (name == self.policy.follow_name and
                                     (member.cp is not None or member.mp is not None)))]
        if len(members) != 1:
            raise TargetChanged(f"Нет свежего HP строки: {name}")
        slot = members[0].slot
        box = self.party.box("name", slot - 1)
        return (box.x1 + box.x2) / 2, (box.y1 + box.y2) / 2, slot

    def select(self, name: str, button: str = "left", twice: bool = False) -> int:
        x, y, slot = self.point(name)
        x, y = self.click_point(x, y, slot)
        revision = self.latest().profile_revision

        def verify() -> None:
            if self.latest().profile_revision != revision:
                raise TargetChanged("Разметка пати изменилась во время наведения")
            if self.guard_approach(self.latest()):
                raise TargetChanged("Сближение остановлено; требуется новое решение")
            if self.point(name)[2] != slot:
                raise TargetChanged("Порядок пати изменился во время наведения")
            current = self.latest()
            self.policy.ingest(current)
            if current.hp is None or current.mp is None:
                raise TargetChanged("Собственные HP/MP стали неизвестны")
            if (name != "@self" and self.policy.needs_single(current, "@self")
                    and current.mp >= self.config.minimum_cast_mp):
                raise TargetChanged("Нужно сначала вылечить себя")

        self.controller.click(button, x, y, verify=verify)
        self.observer.reader.identities.invalidate()
        if twice:
            self.controller.wait(.05)
            self.controller.click(button, x, y, verify=verify)
            self.observer.reader.identities.invalidate()
        return slot

    def click_point(self, x: float, y: float, slot: int) -> tuple[float, float]:
        if slot == 0:
            return x, y
        box = self.party.box("name", slot - 1)
        spread = self.config.party_click_jitter
        margin_x, margin_y = 1 / self.party.width, 1 / self.party.height
        low_x, high_x = box.x1 + margin_x, box.x2 - margin_x
        low_y, high_y = box.y1 + margin_y, box.y2 - margin_y
        if low_x > high_x or low_y > high_y:
            raise TargetChanged("Область клика пати слишком мала")
        return (max(low_x, min(high_x, x + random.uniform(-spread, spread))),
                max(low_y, min(high_y, y + random.uniform(-spread, spread))))

    def stop_approach(self, reason: str) -> None:
        if self.approach_until is None:
            return
        # Подтверждённый пользователем способ отменить игровой бег, включая auto-run от хила.
        self.controller.key("S", 1000)
        self.approach_until = None
        if self.policy.chase_name:
            self.policy.excluded[self.policy.chase_name] = time.monotonic() + self.config.exclude_seconds
        self.policy.chase_name = None
        self.policy.follow_needed = True
        self.report(f"Хилер: сближение остановлено через S — {reason}")

    def guard_approach(self, state: HealerSnapshot | None) -> bool:
        if self.approach_until is None:
            return False
        expired = time.monotonic() >= self.approach_until
        missing = (state is None or time.monotonic() - state.at > 1
                   or self.policy.follow_name not in {m.target_id for m in state.members if m.hp is not None})
        if expired or missing:
            self.stop_approach("лимит времени" if expired else "ведущий / свежий кадр потерян")
            return True
        return False

    def execute(self, decision: HealDecision, state: HealerSnapshot) -> None:
        if time.monotonic() < self.policy.busy_until:
            raise TargetChanged("Каст / Invocation ещё не завершён")
        urgent = (any(hp < .5 or self.policy.strong_damage(target) for target, hp in state.health().items())
                  or bool(self.policy.emergency_slots(time.monotonic())))
        if self.config.humanize and decision.kind == "cast" and decision.skill in {"major", "group"} and not urgent:
            self.controller.wait(random.uniform(0, .1))
        if decision.kind == "probe":
            current = self.latest()
            revised = self.policy.decide(current, time.monotonic())
            if revised.kind != "probe":
                raise TargetChanged("Восстановление Пробелом больше не требуется или запрещено")
            generation = self.controller.pause_generation
            self.policy.begin_stun_probe(current, time.monotonic())
            if self.config.aura_enabled:
                self.aura.before()
            self.controller.key(self.config.stun_probe_key, self.config.stun_probe_hold_ms)
            if self.config.aura_enabled:
                self.aura.after(time.monotonic())
                self.aura.tick(time.monotonic())
            if generation != self.controller.pause_generation:
                self.policy.probe_frame_at = None
                self.policy.probe_baseline.clear()
                self.policy.probe_started_at = None
                raise TargetChanged("Пауза прервала восстановление Пробелом")
            for sample in self.observer.snapshots():
                self.policy.ingest(sample)
            self.report("Восстановление Пробелом: " + ("проверка эффекта продолжается" if self.policy.stun_suspected else "замечен прирост HP"))
            return
        if decision.kind in {"follow", "chase"}:
            if decision.kind == "chase":
                self.approach_until = time.monotonic() + self.config.chase_seconds
            use_key = decision.kind == "follow" and self.config.follow_key is not None
            current_target = self.latest()
            select_with_mouse = decision.kind != "follow" or self.config.follow_select_mouse
            if decision.kind == "follow" and not select_with_mouse and not use_key:
                raise TargetChanged("Для следования без мыши задайте follow_key")
            already_selected = select_with_mouse and decision.kind == "follow" and current_target.follows_selected(decision.target)
            if select_with_mouse and not already_selected:
                self.select(decision.target, "left", not use_key)
            if use_key:
                current = self.latest()
                if already_selected and not current.follows_selected(decision.target):
                    raise TargetChanged(f"Выбранный игрок изменился перед {self.config.follow_key}; требуется повторное выделение")
                revised = self.policy.decide(current, time.monotonic())
                if (revised.kind, revised.target) != (decision.kind, decision.target):
                    raise TargetChanged(f"Приоритет изменился перед {self.config.follow_key}")
                self.controller.key(self.config.follow_key, 100)
                if not select_with_mouse:
                    self.observer.reader.identities.invalidate()
            self.policy.committed(decision, state, time.monotonic())
            if decision.kind == "follow":
                self.approach_until = None
                if select_with_mouse and not already_selected:
                    spread = self.config.cursor_jitter
                    self.controller.move(random.uniform(.5 - spread, .5 + spread),
                                         random.uniform(.5 - spread, .5 + spread), duration=random.uniform(1, 2.5))
        elif decision.kind == "cast":
            if decision.target:
                if decision.target == "@self" and self.config.self_key:
                    self.controller.key(self.config.self_key, 100)
                    self.observer.reader.identities.invalidate()
                else:
                    selected_slot = self.select(decision.target)
                    if self.point(decision.target)[2] != selected_slot:
                        raise TargetChanged("Строка цели изменилась после выделения")
                # Пока двигалась мышь, цель могла сменить строку либо исчезнуть.
                self.point(decision.target)
            current = self.latest()
            if current.destroyer_slots and (decision.skill == "balance"
                                           or decision.skill in {"major", "battle"}
                                           and self.policy.destroyer_protected(current, decision.target)):
                raise TargetChanged("Иконка Разрушителя: баланс и целевой хил защищённого участника запрещены")
            revised = self.policy.decide(current, time.monotonic())
            if (revised.kind, revised.skill, revised.target) != (decision.kind, decision.skill, decision.target):
                raise TargetChanged("После выбора цели приоритет лечения изменился")
            if decision.skill == "invocation":
                # Урон во время подготовки отменяет Invocation до нажатия.
                self.policy.ingest(current)
                if (time.monotonic() - self.policy.last_damage < self.config.quiet_seconds
                        and not self.policy.urgent_invocation(current, time.monotonic())):
                    return
            # Резервируем откат до ввода: неопределённый ACK не означает, что скилл не сработал.
            self.policy.committed(decision, current, time.monotonic())
            self.save_timers()
            if decision.target and decision.target != "@self":
                # Одиночный хил тоже может включить движение; общий бюджет не продлеваем.
                self.approach_until = min(self.approach_until or float("inf"), time.monotonic() + 30)
            key = getattr(self.config, decision.skill + "_key")
            self.report(f"Событие → {key} ({decision.skill}): {decision.reason}; цель={decision.target or 'без выделения'}; "
                        f"HP={current.health()}, свой MP={current.mp:.0%}")
            if self.config.aura_enabled:
                self.aura.before()
            self.controller.key(key, 100)
            if decision.skill == "group":
                # Уточняем таймер после доставки F6, включая ожидание переключения ауры.
                self.policy.ready_at["group"] = max(self.policy.ready_at["group"], time.monotonic()
                                                    + self.config.cast_seconds("group")
                                                    + self.config.group_buff_seconds)
            if self.config.aura_enabled:
                self.aura.after(time.monotonic() + self.config.cast_seconds(decision.skill))
            if decision.skill in {"battle", "major", "group", "mass_major", "avatar", "balance", "purify", "self_rescue", "area_heal"}:
                ambiguous = self.active_cast is not None and self.active_cast[1] > time.monotonic()
                self.active_cast = decision, time.monotonic() + self.config.cast_seconds(decision.skill), ambiguous
                self.cast_revision = current.profile_revision
            if not decision.target or decision.target == "@self":
                self.approach_until = None
            # Отсчитываем полное ожидание после доставки нажатия, а не до записи файла.
            chained = self.policy.chain_heals and decision.skill not in {"invocation", "mp_buff", "mp_buff2"}
            self.policy.busy_until = time.monotonic() + (self.config.heal_press_interval if chained else
                                                        self.config.cast_seconds(decision.skill) + self.config.verify_seconds)
            self.policy.pending = decision, current.health(), self.policy.busy_until
            if decision.skill == "balance":
                self.policy.last_balance = self.policy.busy_until
            self.save_timers()
        self.report(f"Хилер: {decision.kind} {decision.skill or ''} "
                    f"{decision.target or ''} — {decision.reason}")

    def run(self, deadline: float | None = None) -> None:
        deadline = min(time.monotonic() + self.config.max_seconds,
                       deadline if deadline is not None else float("inf"))
        unknown_since = time.monotonic()
        generation = self.controller.pause_generation
        next_log = 0.
        next_state = 0.
        last_warning = None
        self.report(f"Хилер: ведущий — строка №{self.config.follow_slot}; ожидаются HP/MP")
        self.observer.start()
        try:
            while time.monotonic() < deadline:
                self.controller.checkpoint()
                if generation != self.controller.pause_generation:
                    generation = self.controller.pause_generation
                    unknown_since = time.monotonic()
                    self.policy.follow_needed = True
                    self.policy.hp_missing_since.clear()
                    self.loot = AutoLoot(time.monotonic())
                    self.active_cast = None
                states = self.observer.snapshots()
                if self.guard_approach(states[-1] if states else None):
                    continue
                now = time.monotonic()
                if not states or now - states[-1].at > 1:
                    if now >= next_log:
                        self.report("Нет свежих наблюдений: ожидание фонового OCR без ввода")
                        next_log = now + 2
                    self.controller.wait(self.config.poll_seconds)
                    continue
                for state in states:
                    self.policy.ingest(state)
                reader = self.observer.reader
                if isinstance(reader, HealerReader):
                    with reader.profile_lock:
                        matches = states[-1].profile_revision == reader.revision
                        self.own, self.party = reader.own, reader.party
                    if not matches:
                        self.controller.wait(self.config.poll_seconds)
                        continue
                state = self.policy.monitored_state(states[-1])
                if self.config.aura_enabled and state.hp is not None and state.hp > 0 and state.mp is not None:
                    self.aura.tick(time.monotonic())
                self.cancel_cast_if_unneeded(state, now)
                decision = self.policy.decide(state, now)
                state = self.policy.monitored_state(state)
                if self.policy.roster_warning and self.policy.roster_warning != last_warning:
                    self.notify(self.policy.roster_warning)
                    last_warning = self.policy.roster_warning
                publish = getattr(self.notify, "state", None)
                if publish is not None and now >= next_state:
                    publish({"state": state, "reason": decision.reason,
                             "rates": {member.target_id: self.policy.rate(member.target_id) for member in state.members}})
                    next_state = now + .2
                if state.hp is not None and state.mp is not None:
                    unknown_since = now
                elif now - unknown_since > 10 and now >= next_log:
                    self.report("Собственные HP/MP потеряны: ожидание фонового перепоиска без ввода")
                if now >= next_log:
                    text = format_state(self.policy, state, decision, now)
                    logger.debug(text)
                    if logger.isEnabledFor(logging.DEBUG):
                        self.notify(text)
                    next_log = now + 2
                if decision.kind not in {"wait", "stop"}:
                    self.loot.interrupt(now)
                    try:
                        self.execute(decision, state)
                    except TargetChanged as exc:
                        logger.info("Действие хилера отложено: %s", exc)
                if decision.kind == "stop":
                    if state.hp == 0:
                        self.approach_until = None
                        self.aura.on = False
                    raise RuntimeError(decision.reason)
                if self.config.auto_loot and decision.kind == "wait":
                    try:
                        current = self.latest()
                    except TargetChanged:
                        self.controller.wait(self.config.poll_seconds)
                        continue
                    revised = self.policy.decide(current, time.monotonic())
                    safe = (revised.kind == "wait" and time.monotonic() >= self.policy.busy_until
                            and current.hp is not None and current.hp > 0 and current.mp is not None
                            and not self.policy.stun_suspected and not self.policy.emergency_slots(time.monotonic())
                            and not any(self.policy.needs_single(current, target) for target in current.health()))
                    if not safe:
                        self.loot.interrupt(now)
                    elif self.loot.due(time.monotonic()):
                        self.controller.key(self.config.loot_key, 100)
                        self.loot.committed(time.monotonic())
                        logger.debug("Автолут: %s; в серии осталось %s", self.config.loot_key, self.loot.remaining)
                wait = self.config.poll_seconds
                if self.config.auto_loot and self.loot.remaining:
                    wait = min(wait, max(.01, self.loot.next_at - time.monotonic()))
                self.controller.wait(wait)
            self.stop_approach("время сценария истекло")
        finally:
            try:
                if self.config.aura_enabled and self.aura.on and not self.controller.stopped.is_set() and not self.controller.paused.is_set():
                    self.aura.after(time.monotonic(), replace=True)
                    self.aura.tick(time.monotonic())
                if not self.controller.paused.is_set() and not self.controller.stopped.is_set():
                    self.stop_approach("завершение сценария")
            except Exception:
                logger.warning("Не удалось отменить сближение при завершении", exc_info=True)
            finally:
                self.observer.close()

    def recover_unknown_state(self, deadline: float) -> None:
        self.controller.recover_view(deadline)
