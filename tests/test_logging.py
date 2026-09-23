import logging

from l2_agent.logging_setup import setup_logging


def test_logging_has_two_handlers_and_does_not_duplicate(tmp_path):
    logger = logging.getLogger("l2_agent")
    original = logger.handlers[:]
    logger.handlers.clear()
    try:
        assert setup_logging(tmp_path) is setup_logging(tmp_path)
        assert len(logger.handlers) == 2
        logger.info("Проверка логирования")
        assert "Проверка логирования" in (tmp_path / "agent.log").read_text(encoding="utf-8")
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers = original
