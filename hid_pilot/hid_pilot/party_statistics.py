"""
Единое табличное представление статистики персонажа, цели и пати.
"""

from hid_pilot.party import PartyMember


def format_statistics(own: dict[str, float | None], selected: dict[str, object],
                      members: list[PartyMember], rates: dict[str, float], action: str,
                      buffs: dict[str, dict[str, int]] | None = None, *, roster_known: bool = True) -> str:
    def percent(value: float | None) -> str:
        if value is None:
            return "?"
        return "<1%" if 0 < value < .005 else f"{value:.0%}"

    divider = "=" * 60
    player_class = selected.get("player_class") or selected.get("class")
    kind = selected.get("kind") or selected.get("type") or ("player" if player_class else "unknown")
    types = {"player": "игрок", "monster": "монстр", "npc": "NPC", "unknown": "неизвестно"}
    dead = selected.get("dead")
    if selected.get("hp") == 0 or selected.get("death_marker"):
        dead = True
    own_stats = "   ".join(f"{name.upper()} {'—' if own.get(name) is None else percent(own[name])}"
                           for name in ("cp", "hp", "mp"))
    lines = ["СВОИ СТАТЫ", own_stats,
             divider, "ВЫДЕЛЕННАЯ ЦЕЛЬ", f"Тип: {types.get(kind, 'неизвестно')}",
             f"{'Имя' if kind in {'monster', 'npc'} else 'Ник'}: {selected.get('name') or 'не распознан'}"]
    if kind in {"player", "unknown"}:
        lines.extend([f"Класс: {player_class or '?'}", f"Клан: {selected.get('clan') or '?'}"])
    lines.insert(2, f"Уровень: {own['level'] if own.get('level') is not None else '—'}")
    if "summon_hp" in own or "summon_mp" in own:
        lines.insert(3, f"САМОН: HP {percent(own.get('summon_hp'))} · MP {percent(own.get('summon_mp'))}")
    if selected.get("raw_name") and not selected.get("name"):
        lines.append(f"OCR имени: {selected['raw_name']} — подтверждения {selected.get('name_confirmations', 0)}/3")
    lines.append(f"Уровень цели: {selected.get('level') if selected.get('level') is not None else '—'}")
    lines.extend([f"HP цели: {percent(selected.get('hp'))}",
                  f"Состояние: {'мёртвая' if dead is True else 'живая' if dead is False else 'неизвестно'}",
             divider, f"ПАТИ — участников: {len(members) if roster_known else 'неизвестно'}",
             f"{'№':>2} | {'CP':>4} | {'HP':>4} | {'MP':>4} | {'Урон %/с':>9} | Ник"])
    if buffs is not None:
        lines[-1] += " | Бафы скрипта | До обновления, с"
    table_header_index = len(lines) - 1
    buff_rows = {}
    if buffs is not None:
        for member in members:
            active = buffs.get(member.target_id, {})
            buff_rows[member.target_id] = (", ".join(active) or "—",
                                           ", ".join(str(seconds) for seconds in active.values()) or "—")
        name_width = max([len("Ник")] + [len(m.name or 'имя неизвестно') for m in members])
        buff_width = max([len("Бафы скрипта")] + [len(row[0]) for row in buff_rows.values()])
        lines[-1] = lines[-1].replace("Ник | Бафы скрипта |", f"{'Ник':<{name_width}} | {'Бафы скрипта':<{buff_width}} |")
    for member in sorted(members, key=lambda item: item.slot):
        rate = f"{rates[member.target_id] * 100:.1f}%/с" if member.target_id in rates else "?"
        lines.append(f"{member.slot:>2} | {percent(member.cp):>4} | {percent(member.hp):>4} | "
                     f"{percent(member.mp):>4} | {rate:>9} | {member.name or 'имя неизвестно'}")
        if buffs is not None:
            labels, seconds = buff_rows[member.target_id]
            lines[-1] = lines[-1].ljust(len(lines[-1]) + name_width - len(member.name or 'имя неизвестно'))
            lines[-1] += f" | {labels:<{buff_width}} | {seconds}"
    width = max(len(line) for line in lines[table_header_index:])
    classes = ["Класс"] + [member.player_class or "?"
                            for member in sorted(members, key=lambda item: item.slot)]
    for index, value in enumerate(classes, table_header_index):
        lines[index] = lines[index].ljust(width) + " | " + value
    lines.extend([divider, "ТЕКУЩЕЕ ДЕЙСТВИЕ", action])
    return "\n".join(lines)
