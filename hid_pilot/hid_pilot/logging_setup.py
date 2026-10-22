import logging
import json
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from hid_pilot.paths import user_data_dir


class SystemChatFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "kind": getattr(record, "chat_kind", "unknown"),
            "text": record.getMessage(),
            "amount": getattr(record, "chat_amount", None),
            "sp": getattr(record, "chat_sp", None),
            "observed_monotonic": getattr(record, "chat_at", None),
        }, ensure_ascii=False)


def setup_logging(log_dir: Path | None = None) -> logging.Logger:
    logger = logging.getLogger("hid_pilot")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger
    log_dir = log_dir or user_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        log_dir / "hid-pilot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    for handler in (logging.StreamHandler(), file_handler):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    chat_handler = RotatingFileHandler(
        log_dir / "system-chat.jsonl", maxBytes=10_000_000, backupCount=3, encoding="utf-8"
    )
    chat_handler.addFilter(logging.Filter("hid_pilot.system_chat"))
    chat_handler.setFormatter(SystemChatFormatter())
    logger.addHandler(chat_handler)
    # Сбор чата продолжается при смене уровня основного журнала в GUI.
    logging.getLogger("hid_pilot.system_chat").setLevel(logging.INFO)
    return logger
