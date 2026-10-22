import pytest

from hid_pilot.duo import DuoConfig, format_duo_settings, parse_duo_settings


def test_set_format_supports_comments_lists_and_optional_heal():
    config = parse_duo_settings('''
# Настройки как в обычном скрипте.
set next_target 6
set attack 3
set damage_keys "3,F5"
set overhit_keys "F6"
set heal_key ""
set regen_enabled false
set rebuff_minutes 19 # интервал баффа
''')
    assert config.attack == "3"
    assert config.damage_keys == ("3", "F5")
    assert config.overhit_keys == ("F6",)
    assert config.heal_key is None
    assert not config.regen_enabled


def test_all_settings_roundtrip_through_script_and_legacy_json():
    config = DuoConfig(buff_key="F11", heal_key="F9", damage_keys=("F5",), max_hours=7)
    text = format_duo_settings(config)
    assert "set buff_key" in text
    assert "set max_seconds" not in text
    assert parse_duo_settings(text) == config
    assert parse_duo_settings(config.model_dump_json()) == config


@pytest.mark.parametrize("text", ['key R 100', 'set heal_key F9\nset heal_key F8',
                                 'set wrong_field 12', 'set heal_key ALT+F4', 'set attack'])
def test_invalid_settings_are_rejected(text):
    with pytest.raises(ValueError):
        parse_duo_settings(text)
