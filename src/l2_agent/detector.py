import hashlib
import json
import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from l2_agent.capture import CapturedFrame
from l2_agent.dataset import DETECTOR_CLASSES
from l2_agent.roi import NormalizedBox
from l2_agent.world_state import DetectedObject, ObjectObservation

logger = logging.getLogger("l2_agent.detector")
DEFAULT_DETECTOR_PATH = Path("models/detector-v2/model.onnx")


def prepare_image(rgb: np.ndarray, size: int) -> tuple[np.ndarray, float, int, int]:
    height, width = rgb.shape[:2]
    gain = min(size / width, size / height)
    resized_width, resized_height = round(width * gain), round(height * gain)
    left, top = (size - resized_width) // 2, (size - resized_height) // 2
    padded = np.full((size, size, 3), 114, dtype=np.uint8)
    padded[top:top + resized_height, left:left + resized_width] = cv2.resize(
        rgb, (resized_width, resized_height)
    )
    tensor = np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32) / 255
    return tensor, gain, left, top


def decode_output(
    output: np.ndarray, width: int, height: int, gain: float, left: int, top: int,
    classes: tuple[str, ...], confidence: float = 0.35,
) -> tuple[DetectedObject, ...]:
    if output.ndim != 3 or output.shape[:2] != (1, 4 + len(classes)):
        raise ValueError("Неожиданная форма YOLO output")
    predictions = output[0].T
    predictions = predictions[np.isfinite(predictions).all(axis=1)]
    scores = predictions[:, 4:].max(axis=1)
    chosen = (scores >= confidence) & (scores <= 1)
    predictions, scores = predictions[chosen], scores[chosen]
    if not len(scores):
        return ()
    kinds = predictions[:, 4:].argmax(axis=1)
    boxes = predictions[:, :4].copy()
    boxes[:, :2] -= boxes[:, 2:] / 2
    # NMS выполняется отдельно для каждого класса, до обратного преобразования letterbox.
    kept = []
    for kind in np.unique(kinds):
        indices = np.flatnonzero(kinds == kind)
        valid = indices[(boxes[indices, 2:] > 0).all(axis=1)]
        selected = cv2.dnn.NMSBoxes(boxes[valid].tolist(), scores[valid].tolist(),
                                    confidence, 0.7)
        kept.extend(valid[np.asarray(selected, dtype=int).reshape(-1)].tolist())
    objects = []
    for index in sorted(kept, key=lambda i: scores[i], reverse=True)[:200]:
        x, y, w, h = boxes[index]
        x1, x2 = np.clip([(x - left) / gain / width, (x + w - left) / gain / width], 0, 1)
        y1, y2 = np.clip([(y - top) / gain / height, (y + h - top) / gain / height], 0, 1)
        if x2 > x1 and y2 > y1:
            objects.append(DetectedObject(
                kind=classes[int(kinds[index])], confidence=float(scores[index]),
                box=NormalizedBox(x1=x1, y1=y1, x2=x2, y2=y2),
            ))
    return tuple(objects)


class OnnxDetector:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.session = None

    def detect(self, rgb: np.ndarray) -> tuple[DetectedObject, ...]:
        if self.session is None:
            import onnxruntime as ort

            manifest = json.loads(self.path.with_suffix(".json").read_text(encoding="utf-8"))
            if (manifest.get("taxonomy_version") != 2
                    or manifest.get("classes") != list(DETECTOR_CLASSES)
                    or manifest.get("sha256") != hashlib.sha256(self.path.read_bytes()).hexdigest()):
                raise ValueError("Модель и схема классов не соответствуют manifest")
            size = manifest.get("input_size")
            if not isinstance(size, int) or not 32 <= size <= 1536 or size % 32:
                raise ValueError("Неподдерживаемый размер входа детектора")
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            options.add_session_config_entry("session.intra_op.allow_spinning", "0")
            session = ort.InferenceSession(str(self.path), options, providers=["CPUExecutionProvider"])
            if session.get_inputs()[0].shape != [1, 3, size, size]:
                raise ValueError("ONNX input не соответствует manifest")
            self.size = size
            self.session = session
            logger.info("Детектор загружен: %s, CPU, 2 потока", self.path)
        tensor, gain, left, top = prepare_image(rgb, self.size)
        output = self.session.run(None, {self.session.get_inputs()[0].name: tensor})[0]
        return decode_output(output, rgb.shape[1], rgb.shape[0], gain, left, top, DETECTOR_CLASSES)


class DetectorWorker:
    """
    Один кадр в работе, без очереди; результаты принадлежат окну и исходному кадру.
    """

    def __init__(self, provider: OnnxDetector) -> None:
        self.provider = provider
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._generation = 0
        self._key: object = None
        self._result: ObjectObservation | None = None
        self._next_submit = 0.0
        self._last_sequence = -1

    def invalidate(self) -> None:
        with self._lock:
            self._generation += 1
            self._key = None
            self._result = None
            self._last_sequence = -1

    def observe(self, frame: CapturedFrame, enabled: bool, context: object) -> ObjectObservation:
        now = time.monotonic()
        if not enabled or context is None or not 0 <= now - frame.timestamp <= 1:
            self.invalidate()
            return ObjectObservation(reason="Детектор выключен или нет свежего кадра")
        with self._lock:
            if self._closed:
                return ObjectObservation(reason="Детектор остановлен")
            key = (context, frame.rect)
            if key != self._key:
                self._generation += 1
                self._key, self._result, self._last_sequence = key, None, -1
            if (now >= self._next_submit and frame.sequence != self._last_sequence
                    and (self._thread is None or not self._thread.is_alive())):
                self._last_sequence = frame.sequence
                self._next_submit = now + 0.5
                self._thread = threading.Thread(
                    target=self._run, args=(self._generation, frame.model_copy(update={"rgb": frame.rgb.copy()})),
                    daemon=True, name="ObjectDetector",
                )
                self._thread.start()
            result = self._result
            if result is not None:
                if not result.ready:
                    return result
                if (result.timestamp is not None and 0 <= now - result.timestamp <= 1
                        and result.frame_id <= frame.sequence and result.timestamp <= frame.timestamp):
                    return result
        return ObjectObservation(reason="Ожидание свежих детекций")

    def _run(self, generation: int, frame: CapturedFrame) -> None:
        start = time.monotonic()
        try:
            objects = self.provider.detect(frame.rgb)
            result = ObjectObservation(ready=True, objects=objects, frame_id=frame.sequence,
                                       timestamp=frame.timestamp, inference_ms=(time.monotonic() - start) * 1000)
        except Exception:
            logger.exception("Ошибка детектора объектов")
            result = ObjectObservation(reason="Ошибка детектора; подробности в логе")
        with self._lock:
            if not self._closed and generation == self._generation:
                self._result = result
                if not result.ready:
                    self._next_submit = time.monotonic() + 10

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._generation += 1
            self._result = None
        if self._thread is not None:
            self._thread.join(timeout=2)
