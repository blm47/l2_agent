"""
Единое табличное представление статистики персонажа, цели и пати.
"""

from hid_pilot.party import PartyMember


def format_statistics(own: dict[str, float | None], selected: dict[str, object],
                      members: list[PartyMember], rates: dict[str, float], action: str) -> str:
    def percent(value: float | None) -> str:
        return "?" if value is None else f"{value:.0%}"

    divider = "=" * 60
    kind = selected.get("type") or ("player" if selected.get("class") else "unknown")
    types = {"player": "игрок", "monster": "монстр", "npc": "NPC", "unknown": "неизвестно"}
    dead = selected.get("dead")
    lines = ["СВОИ СТАТЫ", f"CP {percent(own.get('cp'))}   HP {percent(own.get('hp'))}   MP {percent(own.get('mp'))}",
             divider, "ВЫДЕЛЕННАЯ ЦЕЛЬ", f"Тип: {types.get(kind, 'неизвестно')}",
             f"{'Имя' if kind in {'monster', 'npc'} else 'Ник'}: {selected.get('name') or 'не распознан'}"]
    if kind in {"player", "unknown"}:
        lines.extend([f"Класс: {selected.get('class') or '?'}", f"Клан: {selected.get('clan') or '?'}"])
    if own.get("level") is not None:
        lines.insert(2, f"Уровень: {own['level']}")
    if selected.get("raw_name") and not selected.get("name"):
        lines.append(f"OCR имени: {selected['raw_name']} — подтверждения {selected.get('name_confirmations', 0)}/6")
    lines.append(f"Уровень цели: {selected.get('level') or '?'}")
    lines.extend([f"HP цели: {percent(selected.get('hp'))}",
                  f"Состояние: {'мёртвая' if dead is True else 'живая' if dead is False else 'неизвестно'}",
             divider, f"ПАТИ — участников: {len(members)}",
             f"{'№':>2} | {'CP':>4} | {'HP':>4} | {'MP':>4} | {'Урон %/с':>9} | Ник"])
    for member in sorted(members, key=lambda item: item.slot):
        rate = f"{rates[member.target_id] * 100:.1f}%/с" if member.target_id in rates else "?"
        lines.append(f"{member.slot:>2} | {percent(member.cp):>4} | {percent(member.hp):>4} | "
                     f"{percent(member.mp):>4} | {rate:>9} | {member.name or 'имя неизвестно'}")
    lines.extend([divider, "ТЕКУЩЕЕ ДЕЙСТВИЕ", action])
    return "\n".join(lines)
