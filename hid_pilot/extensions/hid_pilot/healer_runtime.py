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

from hid_pilot.bars import BarProfile
from hid_pilot.controller import ActionController
from hid_pilot.healer import HealDecision, HealerConfig, HealerSnapshot, HealPolicy
from hid_pilot.healer_log import format_state
from hid_pilot.healer_observer import HealerObserver, HealerReader
from hid_pilot.party import PartyProfile

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
        self.approach_until: float | None = None
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
            if skill not in {"balance", "battle", "major", "mass_major", "group", "avatar", "invocation", "recharge", "purify", "mp_buff", "mp_buff2"}:
                raise ValueError("Неизвестный скилл в файле откатов")
            remaining = float(expiry) - wall
            if not -86400 * 365 <= remaining <= 3600:
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
        return self.policy.monitored_state(states[-1])

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

        def verify() -> None:
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
        if twice:
            self.controller.wait(.05)
            self.controller.click(button, x, y, verify=verify)
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
        if decision.kind == "probe":
            current = self.latest()
            revised = self.policy.decide(current, time.monotonic())
            if revised.kind != "probe":
                raise TargetChanged("Восстановление Пробелом больше не требуется или запрещено")
            generation = self.controller.pause_generation
            self.policy.begin_stun_probe(current, time.monotonic())
            self.controller.key(self.config.stun_probe_key, self.config.stun_probe_hold_ms)
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
            already_selected = decision.kind == "follow" and self.latest().follows_selected(decision.target)
            if not already_selected:
                self.select(decision.target, "left", not use_key)
            if use_key:
                current = self.latest()
                if already_selected and not current.follows_selected(decision.target):
                    raise TargetChanged("Выбранный игрок изменился перед F1; требуется повторное выделение")
                revised = self.policy.decide(current, time.monotonic())
                if (revised.kind, revised.target) != (decision.kind, decision.target):
                    raise TargetChanged("Приоритет изменился перед F1")
                self.controller.key(self.config.follow_key, 100)
            self.policy.committed(decision, state, time.monotonic())
            if decision.kind == "follow":
                self.approach_until = None
                if not already_selected:
                    spread = self.config.cursor_jitter
                    self.controller.move(random.uniform(.5 - spread, .5 + spread),
                                         random.uniform(.5 - spread, .5 + spread))
        elif decision.kind == "cast":
            if decision.target:
                if decision.target == "@self" and self.config.self_key:
                    self.controller.key(self.config.self_key, 100)
                else:
                    selected_slot = self.select(decision.target)
                    if self.point(decision.target)[2] != selected_slot:
                        raise TargetChanged("Строка цели изменилась после выделения")
                # Пока двигалась мышь, цель могла сменить строку либо исчезнуть.
                self.point(decision.target)
            current = self.latest()
            revised = self.policy.decide(current, time.monotonic())
            if (revised.kind, revised.skill, revised.target) != (decision.kind, decision.skill, decision.target):
                raise TargetChanged("После выбора цели приоритет лечения изменился")
            if decision.skill == "invocation":
                # Урон во время подготовки отменяет Invocation до нажатия.
                self.policy.ingest(current)
                if time.monotonic() - self.policy.last_damage < self.config.quiet_seconds:
                    return
            # Резервируем откат до ввода: неопределённый ACK не означает, что скилл не сработал.
            self.policy.committed(decision, current, time.monotonic())
            self.save_timers()
            if decision.target and decision.target != "@self":
                # Одиночный хил тоже может включить движение; общий бюджет не продлеваем.
                self.approach_until = min(self.approach_until or float("inf"), time.monotonic() + 30)
            self.controller.key(getattr(self.config, decision.skill + "_key"), 100)
            if not decision.target or decision.target == "@self":
                self.approach_until = None
            # Отсчитываем полное ожидание после доставки нажатия, а не до записи файла.
            self.policy.busy_until = time.monotonic() + self.config.cast_seconds(decision.skill) + self.config.verify_seconds
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
                states = self.observer.snapshots()
                if self.guard_approach(states[-1] if states else None):
                    continue
                now = time.monotonic()
                if not states or now - states[-1].at > 1:
                    if now - unknown_since > 10:
                        raise RuntimeError("Нет свежих наблюдений хилера более 10 секунд")
                    self.controller.wait(self.config.poll_seconds)
                    continue
                for state in states:
                    self.policy.ingest(state)
                state = self.policy.monitored_state(states[-1])
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
                elif now - unknown_since > 10 and now >= self.policy.busy_until:
                    raise RuntimeError("Собственные HP/MP неизвестны более 10 секунд")
                if now >= next_log:
                    text = format_state(self.policy, state, decision, now)
                    logger.debug(text)
                    if logger.isEnabledFor(logging.DEBUG):
                        self.notify(text)
                    next_log = now + 2
                if decision.kind not in {"wait", "stop"}:
                    try:
                        self.execute(decision, state)
                    except TargetChanged as exc:
                        logger.info("Действие хилера отложено: %s", exc)
                if decision.kind == "stop":
                    if state.hp == 0:
                        self.approach_until = None
                    raise RuntimeError(decision.reason)
                self.controller.wait(self.config.poll_seconds)
            self.stop_approach("время сценария истекло")
        finally:
            try:
                if not self.controller.paused.is_set() and not self.controller.stopped.is_set():
                    self.stop_approach("завершение сценария")
            except Exception:
                logger.warning("Не удалось отменить сближение при завершении", exc_info=True)
            finally:
                self.observer.close()

    def recover_unknown_state(self, deadline: float) -> None:
        self.controller.recover_view(deadline)
