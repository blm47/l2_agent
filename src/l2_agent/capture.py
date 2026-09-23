import logging
import threading
import time
from collections import deque
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from l2_agent.geometry import ClientRect
from l2_agent.windows import ParsecWindowManager

logger = logging.getLogger("l2_agent.capture")


def monitor_region(client: ClientRect, monitor: ClientRect) -> tuple[int, int, int, int]:
    if not (
        monitor.left <= client.left < client.right <= monitor.right
        and monitor.top <= client.top < client.bottom <= monitor.bottom
    ):
        raise ValueError("Разместите клиентскую область Parsec целиком на одном мониторе")
    return (
        client.left - monitor.left,
        client.top - monitor.top,
        client.right - monitor.left,
        client.bottom - monitor.top,
    )


class CapturedFrame(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    sequence: int
    timestamp: float
    rect: ClientRect
    rgb: NDArray[np.uint8]


class CaptureStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    message: str = "Preview выключен"
    fps: float = 0
    frame: CapturedFrame | None = None


class BetterCamBackend:
    def __init__(self) -> None:
        import bettercam

        self.module = bettercam
        self.camera: Any = None
        self.output_key: tuple[int, int] | None = None

    def grab(self, rect: ClientRect) -> NDArray[np.uint8] | None:
        # BetterCam 1.0.0 не публикует координаты DXGI outputs через публичный API.
        factory = getattr(self.module, "__factory")
        for device_index, outputs in enumerate(factory.outputs):
            for output_index, output in enumerate(outputs):
                output.update_desc()
                bounds = output.desc.DesktopCoordinates
                if not output.attached_to_desktop:
                    continue
                monitor = ClientRect(
                    left=bounds.left, top=bounds.top, right=bounds.right, bottom=bounds.bottom
                )
                try:
                    region = monitor_region(rect, monitor)
                except ValueError:
                    continue
                key = (device_index, output_index)
                if key != self.output_key:
                    self.close()
                    self.camera = self.module.create(
                        device_idx=device_index,
                        output_idx=output_index,
                        output_color="RGB",
                        max_buffer_len=1,
                    )
                    self.output_key = key
                    logger.info("Capture переключён на DXGI output %s", key)
                return self.camera.grab(region=region)
        raise ValueError("Разместите клиентскую область Parsec целиком на одном мониторе")

    def close(self) -> None:
        if self.camera is not None:
            # comtypes освобождает COM pointers при удалении ссылки. BetterCam 1.0.0
            # дополнительно вызывает Release вручную, что даёт двойное освобождение.
            self.camera.stop()
            self.camera._duplicator.duplicator = None
            self.camera._duplicator.texture = None
            self.camera._stagesurf.texture = None
            self.camera.release()
            self.camera = None
            self.output_key = None


class CaptureWorker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._target: tuple[int, int] | None = None
        self._generation = 0
        self._status = CaptureStatus()
        self._thread = threading.Thread(target=self._run, name="CaptureWorker", daemon=True)
        self._thread.start()

    def select(self, target: tuple[int, int] | None) -> None:
        with self._lock:
            if target != self._target:
                self._target = target
                self._generation += 1
                self._status = CaptureStatus(
                    message="Ожидание кадра" if target else "Preview выключен"
                )

    def latest(self) -> CaptureStatus:
        with self._lock:
            return self._status

    def close(self) -> bool:
        self._stop.set()
        self._thread.join(timeout=2)
        if self._thread.is_alive():
            logger.error("Capture backend не завершился за 2 секунды")
            return False
        return True

    def _publish(self, generation: int, status: CaptureStatus) -> None:
        with self._lock:
            if generation == self._generation and not self._stop.is_set():
                self._status = status

    def _run(self) -> None:
        backend = None
        com = None
        previous_error = ""
        frame = None
        sequence = 0
        generation_seen = -1
        frame_times: deque[float] = deque(maxlen=60)
        manager = ParsecWindowManager()
        try:
            import comtypes

            com = comtypes
            com.CoInitialize()
            while not self._stop.is_set():
                started = time.monotonic()
                with self._lock:
                    target, generation = self._target, self._generation
                if generation != generation_seen:
                    frame = None
                    frame_times.clear()
                    generation_seen = generation
                if target is None:
                    if backend is not None:
                        backend.close()
                        backend = None
                    self._stop.wait(0.1)
                    continue
                try:
                    snapshot = manager.snapshot(*target)
                    rect = snapshot.rect
                    if rect is None:
                        frame = None
                        frame_times.clear()
                        self._publish(generation, CaptureStatus(message="Окно свёрнуто"))
                        self._stop.wait(0.1)
                        continue
                    if frame is not None and frame.rect != rect:
                        frame = None
                        frame_times.clear()
                    if backend is None:
                        backend = BetterCamBackend()
                    rgb = backend.grab(rect)
                    # Кадр не публикуется, если окно переместилось во время grab.
                    after = manager.snapshot(*target)
                    if after.rect != rect:
                        frame = None
                        frame_times.clear()
                        self._publish(generation, CaptureStatus(message="Геометрия меняется"))
                        self._stop.wait(1 / 30)
                        continue
                    now = time.monotonic()
                    if rgb is not None:
                        if rgb.shape != (rect.height, rect.width, 3) or rgb.dtype != np.uint8:
                            raise ValueError(
                                "Размер или формат кадра не совпадает с областью Parsec"
                            )
                        # Отдельный неизменяемый массив не зависит от памяти backend.
                        rgb = np.array(rgb, copy=True, order="C")
                        rgb.flags.writeable = False
                        sequence += 1
                        frame = CapturedFrame(sequence=sequence, timestamp=now, rect=rect, rgb=rgb)
                        frame_times.append(now)
                    while frame_times and frame_times[0] < now - 1:
                        frame_times.popleft()
                    self._publish(
                        generation,
                        CaptureStatus(
                            message="Захват Parsec" if frame is not None else "Ожидание кадра",
                            fps=float(len(frame_times)),
                            frame=frame,
                        ),
                    )
                    previous_error = ""
                except Exception as exc:
                    message = str(exc) or type(exc).__name__
                    if message != previous_error:
                        logger.exception("Ошибка capture: %s", message)
                        previous_error = message
                    frame = None
                    frame_times.clear()
                    self._publish(generation, CaptureStatus(message=f"Capture: {message}"))
                    if backend is not None:
                        backend.close()
                        backend = None
                    self._stop.wait(1)
                self._stop.wait(max(0, 1 / 30 - (time.monotonic() - started)))
        except Exception as exc:
            logger.exception("Capture worker остановлен из-за ошибки")
            with self._lock:
                self._status = CaptureStatus(message=f"Capture остановлен: {exc}")
        finally:
            if backend is not None:
                backend.close()
            if com is not None:
                com.CoUninitialize()
