import builtins

import pytest

from hid_pilot.self_test import check_observation_start


def test_self_test_prepares_observation_without_input(monkeypatch, tmp_path):
    monkeypatch.setattr("hid_pilot.farm.user_data_dir", lambda: tmp_path)
    check_observation_start(tmp_path)
    assert (tmp_path / "monsters.sqlite3").is_file()


def test_self_test_detects_missing_lazy_observation_module(monkeypatch, tmp_path):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "hid_pilot.observation_debug":
            raise ModuleNotFoundError("hid_pilot.observation_debug")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ModuleNotFoundError, match="observation_debug"):
        check_observation_start(tmp_path)
