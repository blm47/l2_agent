import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_dir: Path = Path("logs")) -> logging.Logger:
    logger = logging.getLogger("l2_agent")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        log_dir / "agent.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    for handler in (logging.StreamHandler(), file_handler):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger
