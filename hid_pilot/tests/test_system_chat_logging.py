import json
import logging
from logging.handlers import RotatingFileHandler

from hid_pilot.logging_setup import setup_logging


def test_chat_log_is_separate_utf8_jsonl_and_survives_main_log_level_change(monkeypatch, tmp_path):
    root = logging.getLogger("hid_pilot")
    chat = logging.getLogger("hid_pilot.system_chat")
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", root.level)
    monkeypatch.setattr(root, "propagate", root.propagate)
    monkeypatch.setattr(chat, "level", chat.level)
    try:
        assert setup_logging(tmp_path) is root
        handler_count = len(root.handlers)
        setup_logging(tmp_path)
        assert len(root.handlers) == handler_count
        root.info("Постороннее сообщение приложения")
        root.setLevel(logging.ERROR)
        chat.info('Ошибка "сидя"\nНовая строка', extra={"chat_kind": "sitting_block",
                  "chat_amount": None, "chat_sp": None, "chat_at": 10.5})
        chat.info("You have acquired 530 XP and 42 SP.", extra={"chat_kind": "experience",
                  "chat_amount": 530, "chat_sp": 42, "chat_at": 11.})
        rows = (tmp_path / "system-chat.jsonl").read_text("utf-8").splitlines()
        assert len(rows) == 2
        first, second = map(json.loads, rows)
        assert first["text"] == 'Ошибка "сидя"\nНовая строка'
        assert first["kind"] == "sitting_block"
        assert first["timestamp"].endswith("+00:00")
        assert first["observed_monotonic"] == 10.5
        assert second["amount"] == 530 and second["sp"] == 42
        handler = next(h for h in root.handlers if isinstance(h, RotatingFileHandler)
                       and h.baseFilename.endswith("system-chat.jsonl"))
        assert handler.maxBytes == 10_000_000 and handler.backupCount == 3
    finally:
        for handler in root.handlers:
            handler.close()
