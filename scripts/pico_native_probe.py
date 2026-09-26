import argparse
import ctypes
import json
import time
from pathlib import Path

import cv2
import win32api
import win32gui

from l2_agent.action_controller import ActionController
from l2_agent.capture import BetterCamBackend
from l2_agent.hard_stop import HardStopHotkey
from l2_agent.logging_setup import setup_logging
from l2_agent.pico import PicoTransport


def main() -> int:
    parser = argparse.ArgumentParser(description="Один ручной тест R через Pico")
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--port", default="COM4")
    args = parser.parse_args()
    logger = setup_logging()
    output = Path("artifacts/pico/native-probe")
    output.mkdir(parents=True, exist_ok=True)
    result = {"hwnd": args.hwnd, "pid": args.pid, "completed": False}
    controller = None
    hotkey = None
    capture = None
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        controller = ActionController(pico=PicoTransport(args.port))
        hotkey = HardStopHotkey(lambda: controller.stop("EMERGENCY — native probe"))
        controller.hard_stop_ready = lambda: hotkey.available
        controller.arm((args.hwnd, args.pid))
        win32gui.SetForegroundWindow(args.hwnd)
        time.sleep(0.3)
        rect = controller.environment.snapshot((args.hwnd, args.pid)).rect
        if rect is None:
            raise ValueError("Нет клиентской области игры")
        capture = BetterCamBackend()

        def save_frame(name: str) -> None:
            for _ in range(30):
                frame = capture.grab(rect)
                if frame is not None:
                    if not cv2.imwrite(str(output / name), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)):
                        raise OSError("Не удалось сохранить кадр")
                    return
                time.sleep(0.05)
            raise TimeoutError("Capture не вернул кадр")

        save_frame("before.png")
        controller.key_down("R", 300)
        time.sleep(0.05)
        result["windows_r_down"] = bool(win32api.GetAsyncKeyState(ord("R")) & 0x8000)
        time.sleep(0.4)
        result["windows_r_released"] = not bool(win32api.GetAsyncKeyState(ord("R")) & 0x8000)
        result["controller_state"] = controller.state
        controller.stop("STOP — native probe завершён")
        time.sleep(0.5)
        save_frame("after.png")
        result["completed"] = True
        logger.info("Pico native probe: %s", result)
        return 0
    except Exception as exc:
        result["error"] = str(exc)
        logger.exception("Pico native probe не завершён")
        return 1
    finally:
        if controller is not None:
            controller.close()
        if hotkey is not None:
            hotkey.close()
        if capture is not None:
            capture.close()
        (output / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    raise SystemExit(main())
