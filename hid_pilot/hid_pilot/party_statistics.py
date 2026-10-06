"""
Единое табличное представление статистики персонажа, цели и пати.
"""

from hid_pilot.party import PartyMember


def format_statistics(own: dict[str, float | None], selected: dict[str, str | None],
                      members: list[PartyMember], rates: dict[str, float], action: str) -> str:
    def percent(value: float | None) -> str:
        return "?" if value is None else f"{value:.0%}"

    divider = "=" * 60
    lines = ["СВОИ СТАТЫ", f"CP {percent(own.get('cp'))}   HP {percent(own.get('hp'))}   MP {percent(own.get('mp'))}",
             divider, "ВЫДЕЛЕННАЯ ЦЕЛЬ", f"Ник: {selected.get('name') or 'не распознан'}",
             f"Класс: {selected.get('class') or '?'}", f"Клан: {selected.get('clan') or '?'}",
             divider, f"ПАТИ — участников: {len(members)}",
             f"{'№':>2} | {'CP':>4} | {'HP':>4} | {'MP':>4} | {'Урон %/с':>9} | Ник"]
    for member in sorted(members, key=lambda item: item.slot):
        rate = f"{rates[member.target_id] * 100:.1f}%/с" if member.target_id in rates else "?"
        lines.append(f"{member.slot:>2} | {percent(member.cp):>4} | {percent(member.hp):>4} | "
                     f"{percent(member.mp):>4} | {rate:>9} | {member.name or 'имя неизвестно'}")
    lines.extend([divider, "ТЕКУЩЕЕ ДЕЙСТВИЕ", action])
    return "\n".join(lines)
