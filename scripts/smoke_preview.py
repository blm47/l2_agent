import argparse
import ctypes
import json
import os
import time
from ctypes import wintypes
from pathlib import Path
from statistics import mean
from typing import Any

import win32api

from l2_agent.logging_setup import setup_logging


class ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        *[
            (name, ctypes.c_size_t)
            for name in (
                "PeakWorkingSetSize",
                "WorkingSetSize",
                "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage",
                "PagefileUsage",
                "PeakPagefileUsage",
                "PrivateUsage",
            )
        ],
    ]


def read_memory() -> dict[str, int]:
    # pywin32 возвращает базовую структуру без PrivateUsage, поэтому читаем EX.
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = ProcessMemoryCounters(cb=ctypes.sizeof(ProcessMemoryCounters))
    if not psapi.GetProcessMemoryInfo(
        int(win32api.GetCurrentProcess()), ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return {"WorkingSetSize": counters.WorkingSetSize, "PrivateUsage": counters.PrivateUsage}


def summarize(
    samples: list[dict[str, float]],
    elapsed: float,
    frames: int,
    max_gap: float,
    error: str | None = None,
) -> dict[str, Any]:
    # Первую минуту исключаем: Qt и DXGI ещё выделяют рабочие буферы.
    baseline = [sample for sample in samples if 60 <= sample["seconds"] < 120]
    tail = [sample for sample in samples if sample["seconds"] >= elapsed - 60]
    growth = None
    if elapsed >= 180 and baseline and tail:
        growth = mean(sample["private_mb"] for sample in tail) - mean(
            sample["private_mb"] for sample in baseline
        )
    passed = error is None and frames > 0 and max_gap <= 3 and (growth is None or growth <= 32)
    return {
        "passed": passed,
        "error": error,
        "ten_minute_run": elapsed >= 600,
        "elapsed_seconds": elapsed,
        "observed_frames": frames,
        "max_frame_gap_seconds": max_gap,
        "private_growth_mb_after_warmup": growth,
        "max_rss_mb": max((sample["rss_mb"] for sample in samples), default=0),
        "max_private_mb": max((sample["private_mb"] for sample in samples), default=0),
        "criteria": {"max_frame_gap_seconds": 3, "max_private_growth_mb": 32},
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test preview без видимого окна GUI")
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--report", type=Path, default=Path("artifacts/preview-smoke.json"))
    args = parser.parse_args()
    if not 1 <= args.seconds <= 3600:
        parser.error("Продолжительность должна быть от 1 до 3600 секунд")
    logger = setup_logging()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
    if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
        raise ctypes.WinError(ctypes.get_last_error())

    from PySide6.QtWidgets import QApplication

    from l2_agent.gui import MainWindow

    app = QApplication([])
    window = MainWindow()
    window.show()
    started = time.monotonic()
    next_report = started
    last_sequence = -1
    observed_frames = 0
    last_frame_at = started
    max_gap = 0.0
    samples: list[dict[str, float]] = []
    error = None
    try:
        while time.monotonic() - started < args.seconds:
            app.processEvents()
            status = window.capture.latest()
            if status.frame is not None and status.frame.sequence != last_sequence:
                now = time.monotonic()
                max_gap = max(max_gap, now - last_frame_at)
                last_frame_at = now
                observed_frames += 1
                last_sequence = status.frame.sequence
            if time.monotonic() >= next_report:
                memory = read_memory()
                samples.append(
                    {
                        "seconds": time.monotonic() - started,
                        "rss_mb": memory["WorkingSetSize"] / 1024**2,
                        "private_mb": memory["PrivateUsage"] / 1024**2,
                        "fps": status.fps,
                    }
                )
                logger.info(
                    "Smoke preview: t=%.1f с; FPS=%.0f; кадров=%s; RSS=%.1f MB; private=%.1f MB; %s",
                    time.monotonic() - started,
                    status.fps,
                    observed_frames,
                    memory["WorkingSetSize"] / 1024**2,
                    memory["PrivateUsage"] / 1024**2,
                    status.message,
                )
                next_report += 5
            time.sleep(0.01)
    except KeyboardInterrupt:
        error = "Прогон прерван"
        logger.warning(error)
    except Exception as exc:
        error = str(exc)
        logger.exception("Ошибка smoke test")
    finally:
        finished = time.monotonic()
        window.close()
    max_gap = max(max_gap, finished - last_frame_at)
    report = summarize(samples, finished - started, observed_frames, max_gap, error)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "Smoke test завершён: кадров=%s; результат=%s; отчёт=%s",
        observed_frames,
        report["passed"],
        args.report,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
