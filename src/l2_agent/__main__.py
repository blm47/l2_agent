import argparse
import sys
from pathlib import Path

from l2_agent.logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(
        description="L2 Agent: Lineage 2 / LU4 / Parsec / M2 advisory mode"
    )
    parser.add_argument("--list-windows", action="store_true", help="Показать окна без GUI")
    parser.add_argument("--pico-port", help="CDC data port Pico, например COM4")
    parser.add_argument("--detector-model", type=Path, default=Path("models/detector-v2/model.onnx"))
    parser.add_argument("--no-detector", action="store_true", help="Запустить без детектора")
    parser.add_argument("--world-source", choices=("cv", "network"), default="cv")
    parser.add_argument("--llm-url", default="http://127.0.0.1:8080")
    parser.add_argument("--llm-model", default="Ternary-Bonsai-2-27B-PTQ1_0.gguf")
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
        from l2_agent.advisory import LocalChatProvider

        app = QApplication(sys.argv[:1])
        model_path = args.detector_model if not args.no_detector and args.detector_model.exists() else None
        window = MainWindow(pico_port=args.pico_port, detector_path=model_path,
                            llm_provider=LocalChatProvider(args.llm_model, args.llm_url),
                            world_source=args.world_source)
        window.show()
        logger.info("Запущен M2 advisory mode; ввод доступен только через ручные тесты")
        return app.exec()
    except Exception:
        logger.exception("Ошибка запуска L2 Agent")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
