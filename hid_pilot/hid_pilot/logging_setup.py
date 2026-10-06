import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from hid_pilot.paths import user_data_dir


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
    return logger
