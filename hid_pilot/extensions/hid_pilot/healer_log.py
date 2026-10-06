"""
Читаемая телеметрия хилера: своя строка, пары участников и причины ожидания.
"""

import math

from hid_pilot.healer import HealDecision, HealerSnapshot, HealPolicy


def percent(value: float | None) -> str:
    return "?" if value is None else f"{value:.0%}"


def support_status(policy: HealPolicy, state: HealerSnapshot, now: float) -> str:
    cfg = policy.config
    common = []
    if now < policy.support_ready_at:
        common.append(f"стартовая задержка {math.ceil(policy.support_ready_at - now)} с")
    if now < policy.busy_until:
        common.append("идёт каст")
    if policy.stun_suspected:
        common.append("проверка стана")
    if state.hp is None or state.mp is None:
        common.append("свои HP/MP неизвестны")
    elif state.mp < cfg.minimum_cast_mp:
        common.append("MP ниже резерва")
    if not policy.health_complete(state):
        common.append("HP/состав пати неизвестны")
    phase = str(getattr(policy, "phase", ""))
    tank = getattr(policy, "tank_target", None)
    if phase == "UNKNOWN":
        common.append("фаза парика неизвестна")
    if any(policy.needs_single(state, target) for target in state.health()
           if not (phase == "COLLECT" and target == tank)):
        common.append("нужно лечение")
    buff = common.copy()
    if cfg.mp_buff_key is None:
        buff.append("выключен")
    if not policy.ready("mp_buff", now):
        buff.append(f"откат {math.ceil(policy.ready_at['mp_buff'] - now)} с")
    invocation = common.copy()
    if phase == "COLLECT":
        invocation.append("сбор парика")
    if state.mp is not None and state.mp >= cfg.invocation_mp:
        invocation.append(f"своё MP {percent(state.mp)} ≥ {percent(cfg.invocation_mp)}")
    if now - policy.last_damage < cfg.quiet_seconds:
        invocation.append(f"до затишья {math.ceil(cfg.quiet_seconds - (now - policy.last_damage))} с")
    if not policy.ready("invocation", now):
        invocation.append(f"откат {math.ceil(policy.ready_at['invocation'] - now)} с")
    buff2 = common.copy()
    if cfg.mp_buff_key is None or cfg.mp_buff2_key is None:
        buff2.append("выключен")
    if not policy.ready("mp_buff2", now):
        buff2.append(f"откат {math.ceil(policy.ready_at['mp_buff2'] - now)} с")
    return (f"F11: {'; '.join(buff) or 'готов'}\n"
            f"F10: {'; '.join(buff2) or 'готов'}\n"
            f"Invocation: {'; '.join(invocation) or 'готов'}")


def format_state(policy: HealPolicy, state: HealerSnapshot, decision: HealDecision, now: float) -> str:
    width = 48
    total = width * 2 + 3
    own = (f"СЕБЯ | CP {percent(state.cp)} | HP {percent(state.hp)} | MP {percent(state.mp)} "
           f"| урон {policy.rate('@self'):.1%}/с")
    lines = [own.center(total)]
    members = sorted(state.members, key=lambda member: member.slot)
    cells = [(f"№{member.slot} CP {percent(member.cp):>4} HP {percent(member.hp):>4} "
              f"MP {percent(member.mp):>4} | {policy.rate(member.target_id):.1%}/с"
              + (f" | {member.name}" if member.name else "")) for member in members]
    for offset in range(0, len(cells), 2):
        left = cells[offset].center(width)
        right = cells[offset + 1].center(width) if offset + 1 < len(cells) else " " * width
        lines.append(f"{left} │ {right}")
    lines.append(f"Действие: {decision.reason}")
    if state.selected_name:
        lines.append(f"Выбран игрок: {state.selected_name} | {state.selected_class or '?'} | "
                     f"{state.selected_clan or 'без клана'}")
    if policy.roster_warning:
        lines.append(policy.roster_warning)
    if policy.hp_missing_since:
        retries = "; ".join(f"№{slot}: {now - since:.0f}/15 сек" for slot, since
                            in sorted(policy.hp_missing_since.items()))
        lines.append("Повторное чтение HP: " + retries)
    lines.append(support_status(policy, state, now))
    if policy.feedback_note:
        lines.append(policy.feedback_note)
    return "\n".join(lines)
