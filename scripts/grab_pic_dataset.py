import argparse
import ctypes
import logging
import math
import random
import sys
import time
from pathlib import Path
from uuid import uuid4

import numpy as np
import win32con
import win32gui
import win32ui

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from l2_agent.capture import CapturedFrame
from l2_agent.dataset import (
    DetectorSample, ObjectAnnotation, load_detector_sample, save_detector_sample,
)
from l2_agent.detector import DEFAULT_DETECTOR_PATH, OnnxDetector
from l2_agent.logging_setup import setup_logging
from l2_agent.windows import GameWindowManager, WindowSnapshot

logger = logging.getLogger("l2_agent.grab_dataset")
OUTPUT_DIR = ROOT / "screenshots"
MIN_INTERVAL = 5.0
MAX_INTERVAL = 20.0
WINDOW_TITLE_PART = "LU4"


def find_lu4_window(title_part: str = WINDOW_TITLE_PART) -> tuple[int | None, str | None]:
    found: list[tuple[int, str]] = []

    def callback(hwnd: int, _: object) -> None:
        title = win32gui.GetWindowText(hwnd)
        if win32gui.IsWindowVisible(hwnd) and title_part.lower() in title.lower():
            found.append((hwnd, title))

    win32gui.EnumWindows(callback, None)
    return found[0] if found else (None, None)


def capture_client_area(window: WindowSnapshot) -> np.ndarray:
    """
    Снимаем исходные RGB-пиксели клиентской области через BitBlt без рамок модели.
    """
    rect = window.rect
    if rect is None or window.minimized or not window.focused:
        raise ValueError("Для capture нужно развернутое окно игры в фокусе")
    desktop = win32gui.GetDesktopWindow()
    dc = win32gui.GetWindowDC(desktop)
    source = memory = bitmap = previous = None
    try:
        source = win32ui.CreateDCFromHandle(dc)
        memory = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source, rect.width, rect.height)
        previous = memory.SelectObject(bitmap)
        memory.BitBlt((0, 0), (rect.width, rect.height), source,
                      (rect.left, rect.top), win32con.SRCCOPY)
        bgra = np.frombuffer(bitmap.GetBitmapBits(True), dtype=np.uint8)
        return bgra.reshape(rect.height, rect.width, 4)[:, :, 2::-1].copy()
    finally:
        if previous is not None:
            memory.SelectObject(previous)
        if bitmap is not None:
            win32gui.DeleteObject(bitmap.GetHandle())
        if memory is not None:
            memory.DeleteDC()
        if source is not None:
            source.DeleteDC()
        win32gui.ReleaseDC(desktop, dc)


def save_preannotated_sample(
    frame: CapturedFrame, window: WindowSnapshot, detector: OnnxDetector, root: Path,
) -> Path:
    """
    Сохраняем свежий кадр, затем атомарно записываем неподтверждённые рамки модели.
    При ошибке inference исходный кадр остаётся unlabeled для ручной разметки.
    """
    directory = save_detector_sample(frame, window, root=root)
    path = directory / "sample.json"
    sample, _ = load_detector_sample(path)
    detections = detector.detect(frame.rgb)
    objects = [ObjectAnnotation(
        kind=obj.kind, box=obj.box, selected=True if obj.kind == "mob_selected" else None,
    ) for obj in detections]
    updated = DetectorSample.model_validate(sample.model_dump() | {
        "annotation_status": "preannotated", "objects": objects,
    })
    temporary = path.with_name(f".{uuid4().hex}.tmp")
    try:
        temporary.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    logger.info("Предразметка сохранена: %s, рамок=%s", directory, len(objects))
    return directory


def main() -> int:
    parser = argparse.ArgumentParser(description="Сбор кадров LU4 с предразметкой YOLO")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--model", type=Path, default=ROOT / DEFAULT_DETECTOR_PATH)
    parser.add_argument("--window-title", default=WINDOW_TITLE_PART)
    parser.add_argument("--min-interval", type=float, default=MIN_INTERVAL)
    parser.add_argument("--max-interval", type=float, default=MAX_INTERVAL)
    args = parser.parse_args()
    if (not math.isfinite(args.min_interval) or not math.isfinite(args.max_interval)
            or not 0 < args.min_interval <= args.max_interval):
        parser.error("Интервалы должны быть конечными: 0 < min <= max")
    setup_logging()
    if not args.model.is_file() or not args.model.with_suffix(".json").is_file():
        logger.error("Не найдена ONNX-модель или её JSON manifest: %s", args.model)
        return 1
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
    if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
        logger.error("Не удалось настроить DPI awareness: %s", ctypes.get_last_error())
        return 1
    hwnd, title = find_lu4_window(args.window_title)
    if hwnd is None:
        logger.error("Окно с заголовком %s не найдено", args.window_title)
        return 1
    manager = GameWindowManager()
    detector = OnnxDetector(args.model)
    pid = manager.snapshot(hwnd).pid
    logger.info("Окно: %s; каталог: %s. Переключитесь в игру. Ctrl+C — остановка.",
                title, args.output.resolve())
    sequence = 0
    try:
        time.sleep(3)
        while win32gui.IsWindow(hwnd):
            window = manager.snapshot(hwnd, pid)
            if window.focused and not window.minimized and window.rect is not None:
                timestamp = time.monotonic()
                rgb = capture_client_area(window)
                current = manager.snapshot(hwnd, pid)
                if current.focused and not current.minimized and current.rect == window.rect:
                    frame = CapturedFrame(sequence=sequence, timestamp=timestamp,
                                          rect=window.rect, rgb=rgb)
                    save_preannotated_sample(frame, current, detector, args.output)
                    sequence += 1
                else:
                    logger.info("Кадр пропущен: фокус или геометрия изменились во время capture")
            else:
                logger.info("Кадр пропущен: игра свёрнута или не в фокусе")
            delay = random.uniform(args.min_interval, args.max_interval)
            logger.info("Следующий кадр через %.1f с", delay)
            time.sleep(delay)
    except KeyboardInterrupt:
        logger.info("Сбор кадров остановлен")
    except Exception:
        logger.exception("Ошибка сбора кадров; сохранённые UUID-папки остаются в каталоге")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
