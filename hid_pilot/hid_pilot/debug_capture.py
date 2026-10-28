"""
Ограниченный сбор кадров игрового окна и ROI без OCR и управления игрой.
"""

import argparse
from datetime import datetime
import json
import logging
from pathlib import Path
import time

import cv2
import numpy as np

from hid_pilot.bars import BarProfile
from hid_pilot.desktop import Desktop, FocusLost, windows
from hid_pilot.logging_setup import setup_logging
from hid_pilot.party import PartyProfile

logger = logging.getLogger("hid_pilot.debug_capture")


def encode_frame(image: np.ndarray, profile: BarProfile | None,
                 party_profile: PartyProfile | None = None) -> dict[str, bytes]:
    """
    Готовит полный кадр и отдельные исходные ROI без дополнительного расширения.
    """
    frames = {"window.jpg": image}
    if profile is not None:
        if (image.shape[1], image.shape[0]) != (profile.width, profile.height):
            raise ValueError("Размер окна отличается от профиля; требуется актуальная разметка")
        boxes = {f"bar_{name}": box for name, box in profile.bars.items()}
        boxes.update({f"number_{name}": box for name, box in profile.numbers.items()})
        for name in ("target_panel", "target_player_panel", "target_hp_presence_box",
                     "target_dead_marker", "own_level", "target_level"):
            box = getattr(profile, name)
            if box is not None:
                boxes[name] = box
        for name, box in boxes.items():
            # Имена пользовательских ROI не должны превращаться в пути файлов.
            safe_name = "".join(char if char.isalnum() or char == "_" else "_" for char in name)
            frames[f"{safe_name}.png"] = profile.crop(image, box)
    if party_profile is not None:
        cropper = BarProfile(width=party_profile.width, height=party_profile.height)
        kinds = ("name", "cp", "hp", "mp") + (("class_icon",) if party_profile.class_icon else ())
        for slot in range(8):
            for kind in kinds:
                frames[f"party_{slot + 1}_{kind}.png"] = cropper.crop(image, party_profile.box(kind, slot))
    encoded = {}
    for name, frame in frames.items():
        ok, data = cv2.imencode(Path(name).suffix, frame,
                               [cv2.IMWRITE_JPEG_QUALITY, 92] if name.endswith(".jpg") else [])
        if not ok:
            raise RuntimeError(f"Не удалось закодировать кадр {name}")
        encoded[name] = data.tobytes()
    return encoded


def save_frame(directory: Path, index: int, image: np.ndarray,
               profile: BarProfile | None, remaining_bytes: int) -> int:
    """
    Записывает комплект целиком только при наличии места в лимите.
    """
    encoded = encode_frame(image, profile)
    size = sum(len(value) for value in encoded.values())
    if size > remaining_bytes:
        return 0
    frame_dir = directory / f"{index:06d}"
    frame_dir.mkdir()
    for name, data in encoded.items():
        (frame_dir / name).write_bytes(data)
    return size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="Показать окна и HWND")
    parser.add_argument("--title", help="Уникальная часть названия окна, например Sangry")
    parser.add_argument("--hwnd", type=int, help="Точный HWND вместо названия")
    parser.add_argument("--profile", type=Path, help="Текущий farm-bars.json МА")
    parser.add_argument("--output", type=Path, default=Path("artifacts/debug-capture"))
    parser.add_argument("--seconds", type=float, default=180)
    parser.add_argument("--fps", type=float, default=5)
    parser.add_argument("--max-mb", type=int, default=512)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 1800 or not .5 <= args.fps <= 15 or not 1 <= args.max_mb <= 2048:
        parser.error("Лимиты: seconds 1..1800, fps 0.5..15, max-mb 1..2048")
    setup_logging()
    available = windows()
    if args.list:
        for target in available:
            logger.info("HWND=%s, %s, %s", target.hwnd, target.title, target.rect)
        return 0
    if not args.title and args.hwnd is None:
        parser.error("Задайте --title или --hwnd; список окон: --list")
    matches = [target for target in available if
               (target.hwnd == args.hwnd if args.hwnd is not None else
                args.title.casefold() in target.title.casefold())]
    if len(matches) != 1:
        parser.error("Нужно ровно одно подходящее окно; уточните --title или --hwnd")
    profile = BarProfile.model_validate_json(args.profile.read_text("utf-8")) if args.profile else None
    directory = args.output / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    directory.mkdir(parents=True)
    target = matches[0]
    metadata = {"window": target.model_dump(), "fps": args.fps, "seconds": args.seconds,
                "profile": profile.model_dump(mode="json") if profile else None}
    manifest = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
    (directory / "session.json").write_bytes(manifest)
    used = len(manifest)
    limit = args.max_mb * 1024 * 1024
    desktop = Desktop(target)
    started = time.monotonic()
    count = 0
    waiting = False
    logger.info("Сбор кадров: %s; активируйте игру вручную. Завершение: Ctrl+C", directory.resolve())
    try:
        with (directory / "frames.jsonl").open("w", encoding="utf-8") as journal:
            while time.monotonic() - started < args.seconds:
                tick = time.monotonic()
                captured_at = datetime.now().isoformat(timespec="milliseconds")
                try:
                    image = desktop.capture()
                except FocusLost:
                    if not waiting:
                        logger.info("Окно не активно или свёрнуто: сбор приостановлен без перехвата фокуса")
                    waiting = True
                    time.sleep(1 / args.fps)
                    continue
                waiting = False
                row = json.dumps({"frame": f"{count:06d}", "captured_at": captured_at,
                                  "elapsed": round(tick - started, 3),
                                  "label": None}, ensure_ascii=False) + "\n"
                row_size = len(row.encode("utf-8"))
                size = save_frame(directory, count, image, profile, limit - used - row_size)
                if not size:
                    logger.info("Достигнут лимит хранения %s MiB", args.max_mb)
                    break
                journal.write(row)
                journal.flush()
                used += size + row_size
                count += 1
                time.sleep(max(0, 1 / args.fps - (time.monotonic() - tick)))
    except KeyboardInterrupt:
        logger.info("Сбор остановлен пользователем")
    except (RuntimeError, ValueError, OSError, cv2.error):
        logger.exception("Сбор кадров остановлен из-за ошибки окна или записи")
        return 1
    finally:
        logger.info("Сохранено %s кадров, %.1f MiB: %s", count, used / 1024**2, directory.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
