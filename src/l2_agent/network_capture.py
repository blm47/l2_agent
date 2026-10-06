"""
Ограниченный пассивный сбор одной TCP-сессии через установленный dumpcap.
"""

import argparse
import ipaddress
import logging
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress

from l2_agent.logging_setup import setup_logging

logger = logging.getLogger("l2_agent.network_capture")


class CaptureConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    interface: int = Field(ge=1, strict=True)
    client_ip: IPvAnyAddress
    client_port: int = Field(ge=1, le=65535, strict=True)
    server_ip: IPvAnyAddress
    server_port: int = Field(ge=1, le=65535, strict=True)
    duration: int = Field(default=30, ge=1, le=300, strict=True)
    max_kib: int = Field(default=16384, ge=1, le=65536, strict=True)

    def capture_filter(self) -> str:
        return (
            f"tcp and ((src host {self.client_ip} and src port {self.client_port} "
            f"and dst host {self.server_ip} and dst port {self.server_port}) or "
            f"(src host {self.server_ip} and src port {self.server_port} "
            f"and dst host {self.client_ip} and dst port {self.client_port}))"
        )


def capture_command(executable: str, config: CaptureConfig, output: Path) -> list[str]:
    return [
        executable, "-i", str(config.interface), "-p", "-f", config.capture_filter(),
        "-a", f"duration:{config.duration}", "-a", f"filesize:{config.max_kib}",
        "-B", "4", "-w", str(output),
    ]


def capture_connection(config: CaptureConfig, output: Path, executable: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    # Резервирование исключает перезапись существующего дампа.
    with output.open("xb"):
        pass
    logger.info("Начат пассивный capture TCP, лимит %s секунд", config.duration)
    try:
        subprocess.run(
            capture_command(executable, config, output), check=True,
            timeout=config.duration + 15, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        logger.exception("Ошибка capture; неполный файл сохранён для диагностики")
        raise
    logger.info("Capture завершён: %s", output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Пассивный capture TCP-сессии LU4")
    parser.add_argument("--dumpcap", default=shutil.which("dumpcap"))
    parser.add_argument("--interface", type=int, required=True)
    parser.add_argument("--client-ip", type=ipaddress.ip_address, required=True)
    parser.add_argument("--client-port", type=int, required=True)
    parser.add_argument("--server-ip", type=ipaddress.ip_address, required=True)
    parser.add_argument("--server-port", type=int, required=True)
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    setup_logging()
    if not args.dumpcap:
        logger.error("dumpcap не найден; установите Wireshark/Npcap или задайте --dumpcap")
        return 1
    try:
        config = CaptureConfig(
            interface=args.interface, client_ip=args.client_ip, client_port=args.client_port,
            server_ip=args.server_ip, server_port=args.server_port, duration=args.duration,
        )
        capture_connection(config, args.output, args.dumpcap)
    except (ValueError, OSError, subprocess.SubprocessError):
        logger.exception("Не удалось выполнить сбор трафика")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
