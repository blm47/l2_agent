import argparse
import ctypes
import hmac
import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from hid_pilot.api_runtime import (
    AlignmentRequest,
    CameraRequest,
    PilotApiRuntime,
    SessionRequest,
    StartRequest,
)
from hid_pilot.controller import Stopped
from hid_pilot.desktop import Target, windows
from hid_pilot.hard_stop import HardStopHotkey
from hid_pilot.logging_setup import setup_logging

logger = logging.getLogger("hid_pilot.api_server")


def handler_for(runtime: PilotApiRuntime, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            # Заголовок Authorization и содержимое кадров в журнал не попадают.
            logger.debug("API: %s", format % args)

        def send_json(self, code: int, value: dict) -> None:
            payload = json.dumps(value, allow_nan=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def authorized(self) -> bool:
            actual = self.headers.get("Authorization", "")
            return hmac.compare_digest(actual, "Bearer " + token)

        def do_GET(self) -> None:
            if not self.authorized():
                self.send_json(401, {"error": "Нет API-токена"})
                return
            try:
                if self.path == "/v1/status":
                    self.send_json(200, runtime.status())
                elif self.path == "/v1/scripts":
                    self.send_json(200, {"scripts": runtime.catalogue()})
                elif self.path == "/v1/frame":
                    self.send_json(200, runtime.frame())
                else:
                    self.send_json(404, {"error": "Неизвестный метод"})
            except (ValueError, RuntimeError, Stopped) as exc:
                self.send_json(409, {"error": str(exc)})
            except OSError:
                logger.exception("Ошибка API capture")
                self.send_json(500, {"error": "Ошибка capture; см. журнал"})

        def do_POST(self) -> None:
            if not self.authorized():
                self.send_json(401, {"error": "Нет API-токена"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16384:
                    raise ValueError("Некорректный размер запроса")
                data = json.loads(self.rfile.read(size))
                if self.path == "/v1/start":
                    result = runtime.start(StartRequest.model_validate(data).script_id)
                elif self.path == "/v1/camera":
                    runtime.camera(CameraRequest.model_validate(data))
                    result = {"ok": True}
                elif self.path == "/v1/align-camera":
                    runtime.align_camera(AlignmentRequest.model_validate(data))
                    result = {"ok": True}
                elif self.path in {"/v1/stop", "/v1/heartbeat", "/v1/pause", "/v1/resume"}:
                    session = SessionRequest.model_validate(data).session_id
                    if self.path == "/v1/stop":
                        runtime.stop(session)
                    elif self.path == "/v1/heartbeat":
                        runtime.heartbeat(session)
                    else:
                        runtime.pause(session, self.path == "/v1/pause")
                    result = {"ok": True}
                else:
                    self.send_json(404, {"error": "Неизвестный метод"})
                    return
                self.send_json(200, result)
            except (ValueError, RuntimeError, Stopped) as exc:
                self.send_json(409, {"error": str(exc)})
            except OSError:
                logger.exception("Ошибка API ввода")
                try:
                    runtime.stop()
                except OSError:
                    logger.exception("STOP API не подтверждён; действует watchdog платы")
                self.send_json(500, {"error": "Ошибка ввода; STOP"})
    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Локальный API HID Pilot для внешнего агента")
    parser.add_argument("--scripts", type=Path, required=True)
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--farmer-title", required=True)
    parser.add_argument("--support-title", required=True)
    parser.add_argument("--pico-port", required=True)
    parser.add_argument("--api-file", type=Path, required=True)
    parser.add_argument("--listen-port", type=int, default=0)
    args = parser.parse_args()
    setup_logging()
    runtime = server = hotkey = None
    closed = threading.Event()
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        available = windows()

        def find(title: str) -> Target:
            matches = [window for window in available if window.title == title]
            if len(matches) != 1:
                raise ValueError(f"Нужно одно окно с точным заголовком «{title}», найдено {len(matches)}")
            return matches[0]

        farmer, support = find(args.farmer_title), find(args.support_title)
        if farmer.hwnd == support.hwnd:
            raise ValueError("Нужны разные окна МА и саппорта")
        hotkey = HardStopHotkey(lambda: runtime.stop() if runtime else None)
        if not hotkey.available:
            raise RuntimeError("Hard-stop занят или недоступен; завершите другой управляющий Pilot")
        runtime = PilotApiRuntime(args.scripts, args.profiles, farmer, support,
                                  args.pico_port, lambda: hotkey.available)
        if not runtime.catalogue():
            raise ValueError("API-форки не найдены")
        token = secrets.token_urlsafe(32)
        server = ThreadingHTTPServer(("127.0.0.1", args.listen_port), handler_for(runtime, token))
        server.daemon_threads = True
        args.api_file.parent.mkdir(parents=True, exist_ok=True)
        with args.api_file.open("x", encoding="utf-8") as stream:
            json.dump({"protocol": 1, "url": f"http://127.0.0.1:{server.server_port}",
                       "token": token}, stream)

        def watch() -> None:
            while not closed.wait(.1):
                runtime.check_lease()

        threading.Thread(target=watch, daemon=True, name="ApiClientWatchdog").start()
        logger.info("API готов: %s; фарм ещё не запущен", args.api_file)
        server.serve_forever(poll_interval=.1)
    except KeyboardInterrupt:
        logger.info("API остановлен пользователем")
    except Exception:
        logger.exception("API не запущен или завершён с ошибкой")
        return 1
    finally:
        closed.set()
        if runtime is not None:
            runtime.stop()
            if runtime.worker is not None:
                runtime.worker.join(timeout=3)
        if server is not None:
            server.server_close()
        if hotkey is not None:
            hotkey.close()
        # Чужой или старый файл сессии не удаляем при неудачном запуске.
        if args.api_file.is_file() and "token" in locals():
            try:
                if json.loads(args.api_file.read_text()).get("token") == token:
                    args.api_file.unlink()
            except (OSError, ValueError):
                logger.exception("Не удалось убрать файл API-сессии")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
