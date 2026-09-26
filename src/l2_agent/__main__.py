import argparse
import sys

from l2_agent.logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(
        description="L2 Agent: Lineage 2 / LU4 / Parsec / настройка восприятия M1"
    )
    parser.add_argument("--list-windows", action="store_true", help="Показать окна без GUI")
    parser.add_argument("--pico-port", help="CDC data port Pico, например COM4")
    args = parser.parse_args()
    logger = setup_logging()
    if sys.platform != "win32":
        logger.error("Приложение поддерживает только Windows")
        return 1
    try:
        import ctypes

        # DPI awareness задаётся до создания GUI и чтения координат.
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
        if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            raise ctypes.WinError(ctypes.get_last_error())

        from l2_agent.windows import GameWindowManager

        if args.list_windows:
            for snapshot in GameWindowManager().discover():
                logger.info("Окно Lineage 2 / LU4 / Parsec: %s", snapshot.model_dump_json())
            return 0

        from PySide6.QtWidgets import QApplication

        from l2_agent.gui import MainWindow

        app = QApplication(sys.argv[:1])
        window = MainWindow(pico_port=args.pico_port)
        window.show()
        logger.info("Запущен M1: настройка ROI; ввод доступен только через ручные тесты")
        return app.exec()
    except Exception:
        logger.exception("Ошибка запуска L2 Agent")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
