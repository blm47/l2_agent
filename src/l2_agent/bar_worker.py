import logging
import threading

from l2_agent.bar_detection import AutoBarDetector, DetectionResult
from l2_agent.capture import CapturedFrame

logger = logging.getLogger("l2_agent.bar_worker")


class BarDetectionWorker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._result: tuple[int, CapturedFrame, DetectionResult] | None = None
        self._closed = False
        self._detector = AutoBarDetector()

    def submit(self, generation: int, frame: CapturedFrame) -> bool:
        with self._lock:
            if self._closed or (self._thread is not None and self._thread.is_alive()):
                return False
            self._result = None
            self._thread = threading.Thread(
                target=self._run,
                args=(generation, frame),
                name="BarDetection",
                daemon=True,
            )
            self._thread.start()
            return True

    def take_result(self) -> tuple[int, CapturedFrame, DetectionResult] | None:
        with self._lock:
            result, self._result = self._result, None
            return result

    def _run(self, generation: int, frame: CapturedFrame) -> None:
        try:
            result = self._detector.detect(frame.rgb)
        except ImportError:
            logger.exception("Зависимости автоматического поиска не установлены")
            result = DetectionResult(
                message="Нужен extra perception: uv sync --extra dev --extra capture --extra perception"
            )
        except Exception as exc:
            logger.exception("Ошибка автоматического поиска полос")
            result = DetectionResult(message=f"Поиск не выполнен: {exc}")
        with self._lock:
            if not self._closed:
                self._result = generation, frame, result

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._result = None
        if self._thread is not None:
            self._thread.join(timeout=2)
