"""
Восстановление ролей окон по устойчивому имени клиента без зависимости от порядка EnumWindows.
"""

from hid_pilot.desktop import Target


def match_session_window(targets: list[Target], saved: Target) -> Target | None:
    for target in targets:
        if (target.hwnd, target.pid, target.title) == (saved.hwnd, saved.pid, saved.title):
            return target
    matches = [target for target in targets if target.title == saved.title]
    return matches[0] if len(matches) == 1 else None
