from hid_pilot.party import PartyMember
from hid_pilot.party_statistics import format_statistics


def test_sections_follow_requested_order_and_columns_stay_aligned():
    members = [PartyMember(slot=2, cp=1., hp=.5, mp=None, name="Second"),
               PartyMember(slot=1, cp=.9, hp=1., mp=.7, name="First")]
    text = format_statistics({"cp": 1., "hp": .8, "mp": .6},
                             {"name": "Leader", "class": "Hawkeye", "clan": "AES"},
                             members, {"@slot1": .125, "@slot2": 0.}, "Следование")
    sections = text.split("=" * 60)
    assert len(sections) == 4
    assert "СВОИ СТАТЫ" in sections[0] and "HP 80%" in sections[0]
    assert "Ник: Leader" in sections[1] and "Класс: Hawkeye" in sections[1] and "Клан: AES" in sections[1]
    rows = [line for line in sections[2].splitlines() if "|" in line]
    assert [part.strip() for part in rows[0].split("|")] == ["№", "CP", "HP", "MP", "Урон %/с", "Ник", "Класс"]
    assert [part.strip() for part in rows[1].split("|")] == ["1", "90%", "100%", "70%", "12.5%/с", "First", "?"]
    assert [part.strip() for part in rows[2].split("|")] == ["2", "100%", "50%", "?", "0.0%/с", "Second", "?"]
    assert len({tuple(index for index, char in enumerate(row) if char == "|") for row in rows}) == 1
    assert sections[3].strip() == "ТЕКУЩЕЕ ДЕЙСТВИЕ\nСледование"


def test_missing_observations_are_not_rendered_as_zero_or_full_health():
    text = format_statistics({}, {}, [PartyMember(slot=1)], {}, "Нет свежего кадра")
    assert "СВОИ СТАТЫ\nCP —   HP —   MP —" in text
    assert "Уровень: —" in text and "Уровень цели: —" in text
    assert "Ник: не распознан" in text
    assert " 1 |    ? |    ? |    ? |         ? | имя неизвестно" in text


def test_recognized_levels_are_always_shown():
    text = format_statistics({"level": 20}, {"level": 25}, [], {}, "Наблюдение")
    assert "Уровень: 20" in text
    assert "Уровень цели: 25" in text


def test_buff_columns_show_matching_lists_even_without_member_names():
    text = format_statistics({}, {}, [PartyMember(slot=1), PartyMember(slot=2, name="LongName")],
                             {}, "Ожидание", buffs={"@slot1": {"Клара": 600, "РШ": 0}})
    rows = [line for line in text.splitlines() if "|" in line]
    assert [c.strip() for c in rows[0].split("|")][-3:] == ["Бафы скрипта", "До обновления, с", "Класс"]
    assert [c.strip() for c in rows[1].split("|")][-3:] == ["Клара, РШ", "600, 0", "?"]
    assert [c.strip() for c in rows[2].split("|")][-3:] == ["—", "—", "?"]
    assert len({tuple(i for i, ch in enumerate(row) if ch == "|") for row in rows}) == 1


def test_class_column_shows_recognized_class_and_unknown_placeholder():
    text = format_statistics({}, {}, [PartyMember(slot=1, name="EE", player_class="Elven Elder"),
                                      PartyMember(slot=2, name="Other")], {}, "Наблюдение")
    rows = [line for line in text.splitlines() if "|" in line]
    assert [row.split("|")[-1].strip() for row in rows] == ["Класс", "Elven Elder", "?"]
    assert len({tuple(i for i, ch in enumerate(row) if ch == "|") for row in rows}) == 1
