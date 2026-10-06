"""
Установка на явно выбранный Pico с проверкой носителя и резервной копией файлов.
"""

import hashlib
import json
import logging
import re
import struct
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from hid_pilot.paths import resource_path, user_data_dir
from hid_pilot.transport import PicoTransport

logger = logging.getLogger("hid_pilot.firmware")
BOARDS = {"raspberry_pi_pico": "Raspberry Pi Pico (RP2040)",
          "raspberry_pi_pico_w": "Raspberry Pi Pico W (RP2040)"}
FIRMWARE_FILES = ("control.py", "boot.py", "code.py")
PROTOCOL = "HID_PILOT_V3"
RP2040_FAMILY = 0xE48BFF56


class PicoVolume(BaseModel):
    model_config = ConfigDict(frozen=True)
    root: Path
    serial: int
    kind: Literal["bootloader", "circuitpython"]
    board: str
    uid: str = ""


def volume_info(root: Path) -> tuple[str, int]:
    import win32api
    import win32file

    if root.resolve().parent != root.resolve() or win32file.GetDriveType(str(root)) != 2:
        raise ValueError("Выберите корень съёмного USB-диска Pico")
    label, serial, *_ = win32api.GetVolumeInformation(str(root))
    return label, serial


def identify_volume(root: Path) -> PicoVolume:
    label, serial = volume_info(root)
    if label == "RPI-RP2":
        info = (root / "INFO_UF2.TXT").read_text("utf-8", errors="replace")
        if not re.search(r"Board-ID:\s*RPI-RP2\b", info) or "RP2040" not in info:
            raise ValueError("BOOTSEL-носитель не подтверждён как RP2040")
        return PicoVolume(root=root, serial=serial, kind="bootloader", board="rp2040")
    if label == "CIRCUITPY":
        info = (root / "boot_out.txt").read_text("utf-8", errors="replace")
        board = re.search(r"Board ID:\s*(\S+)", info)
        uid = re.search(r"UID:\s*([0-9A-Fa-f]+)", info)
        if board is None or board[1] not in BOARDS:
            raise ValueError("Поддерживаются только Raspberry Pi Pico / Pico W на RP2040")
        return PicoVolume(root=root, serial=serial, kind="circuitpython",
                          board=board[1], uid=uid[1].upper() if uid else "")
    raise ValueError("Носитель не является BOOTSEL или CIRCUITPY Pico")


def discover_volumes() -> list[PicoVolume]:
    import win32api

    result = []
    for drive in win32api.GetLogicalDriveStrings().split("\0"):
        if not drive:
            continue
        try:
            result.append(identify_volume(Path(drive)))
        except (OSError, ValueError):
            continue
        except Exception:
            # Недоступный съёмный носитель не должен прерывать поиск остальных.
            logger.debug("USB-носитель недоступен: %s", drive, exc_info=True)
    return result


def validate_volume(volume: PicoVolume, kind: str) -> Path:
    if volume.kind != kind or identify_volume(volume.root) != volume:
        raise ValueError("Плата отключена, заменена либо изменила режим; обновите список Pico")
    return volume.root.resolve()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_uf2(path: Path, *, multi_family: bool = False) -> None:
    data = path.read_bytes()
    if not data or len(data) > 4_000_000 or len(data) % 512:
        raise ValueError("Некорректный размер UF2")
    count = len(data) // 512
    families: dict[int, list[tuple[int, int]]] = {}
    for index in range(count):
        block = data[index * 512:(index + 1) * 512]
        magic0, magic1, flags, _address, size, number, total, family = struct.unpack_from("<8I", block)
        if (magic0 != 0x0A324655 or magic1 != 0x9E5D5157
                or struct.unpack_from("<I", block, 508)[0] != 0x0AB16F30
                or not flags & 0x2000
                or family not in ({RP2040_FAMILY, 0xE48BFF57} if multi_family else {RP2040_FAMILY})
                or not 0 < size <= 476):
            raise ValueError("UF2 не является согласованной прошивкой RP2040")
        families.setdefault(family, []).append((number, total))
    if RP2040_FAMILY not in families:
        raise ValueError("UF2 не содержит прошивки RP2040")
    for blocks in families.values():
        if (sorted(number for number, _ in blocks) != list(range(len(blocks)))
                or any(total != len(blocks) for _, total in blocks)):
            raise ValueError("UF2 содержит неполный или повторяющийся набор блоков")


def vendor_asset(name: str) -> Path:
    directory = resource_path("firmware/vendor")
    manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
    entry = manifest["assets"].get(name)
    if entry is None:
        raise ValueError("В сборке отсутствует выбранная прошивка")
    path = directory / name
    if digest(path) != entry["sha256"]:
        raise ValueError("Checksum встроенной прошивки не совпадает")
    validate_uf2(path, multi_family=name == "flash_nuke.uf2")
    return path


def backup_files(volume: PicoVolume, directory: Path | None = None) -> Path:
    root = validate_volume(volume, "circuitpython")
    destination = directory or user_data_dir() / "pico-backups"
    destination = destination.resolve()
    if destination.is_relative_to(root):
        raise ValueError("Резервная копия должна находиться на компьютере")
    files = []
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError("Ссылки на CIRCUITPY не поддерживаются")
        if path.is_file():
            total += path.stat().st_size
            files.append(path)
            if len(files) > 512 or total > 4_000_000:
                raise ValueError("Резервная копия превышает лимит 512 файлов / 4 MB")
    backup = destination / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    backup.mkdir(parents=True)
    checksums = {}
    for source in files:
        validate_volume(volume, "circuitpython")
        relative = source.relative_to(root)
        target = backup / "files" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = source.read_bytes()
        target.write_bytes(payload)
        checksums[relative.as_posix()] = hashlib.sha256(payload).hexdigest()
    (backup / "manifest.json").write_text(json.dumps({
        "volume": volume.model_dump(mode="json"), "files": checksums,
        "scope": "CIRCUITPY files; excludes firmware image and NVM",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Резервная копия файлов Pico сохранена: %s", backup)
    return backup


def install_scripts(volume: PicoVolume, notify: Callable[[str], None],
                    backup_directory: Path | None = None) -> Path:
    payloads = {name: resource_path(f"firmware/{name}").read_bytes() for name in FIRMWARE_FILES}
    backup = backup_files(volume, backup_directory)
    notify(f"Резервная копия файлов: {backup}")
    staged = []
    try:
        for name, payload in payloads.items():
            root = validate_volume(volume, "circuitpython")
            path = root / f".hidpilot-{name}.new"
            if path.exists():
                raise ValueError("На плате остались файлы незавершённой установки; проверьте резервную копию")
            with path.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                import os
                os.fsync(handle.fileno())
            staged.append(path)
            if path.read_bytes() != payload:
                raise OSError(f"Запись {name} на Pico не подтверждена")
        for name, path in zip(payloads, staged, strict=True):
            root = validate_volume(volume, "circuitpython")
            path.replace(root / name)
            if (root / name).read_bytes() != payloads[name]:
                raise OSError(f"Checksum {name} на Pico не совпадает")
        notify("HID_PILOT_V3 записана и файлы проверены. Безопасно извлеките CIRCUITPY, "
               "переподключите Pico без BOOTSEL и нажмите «Проверить прошивку».")
        logger.info("Файлы HID_PILOT_V3 установлены: %s", volume.root)
    except Exception:
        logger.exception("Установка Pico прервана; резервная копия: %s", backup)
        notify(f"Установка прервана. Резервная копия: {backup}. Проверьте плату перед запуском.")
        raise
    finally:
        for path in staged:
            try:
                validate_volume(volume, "circuitpython")
                path.unlink(missing_ok=True)
            except (OSError, ValueError):
                logger.debug("Временный файл Pico не удалён: %s", path, exc_info=True)
    return backup


def flash_runtime(volume: PicoVolume, board: str, *, erase: bool = False) -> None:
    if board not in BOARDS:
        raise ValueError("Выберите поддерживаемую модель Pico")
    source = vendor_asset("flash_nuke.uf2" if erase else f"{board}.uf2")
    root = validate_volume(volume, "bootloader")
    target = root / "HIDPILOT.UF2"
    # BOOTSEL исчезает после записи UF2: сверка файла на виртуальном диске невозможна.
    # Успех записи не считается проверкой установленной прошивки.
    with target.open("wb") as handle:
        handle.write(source.read_bytes())
        handle.flush()
    logger.info("UF2 отправлен на выбранный Pico: %s; полный сброс=%s", volume.root, erase)


def pico_ports(uid: str = "") -> list[str]:
    from serial.tools import list_ports

    return [port.device for port in list_ports.comports()
            if port.vid in {0x239A, 0x2E8A}
            and (not uid or (port.serial_number or "").upper() == uid.upper())]


def verify_firmware(volume: PicoVolume | None, port: str = "") -> tuple[str, str]:
    if volume is not None:
        root = validate_volume(volume, "circuitpython")
        for name in FIRMWARE_FILES:
            if digest(root / name) != digest(resource_path(f"firmware/{name}")):
                raise ValueError(f"{name} отличается от встроенной прошивки; установите обновление")
        if port and volume.uid and port not in pico_ports(volume.uid):
            raise ValueError("Выбранный COM-порт не принадлежит Pico с UID выбранного CIRCUITPY")
    candidates = [port] if port else pico_ports(volume.uid if volume else "")
    errors = []
    for candidate in candidates:
        transport = None
        try:
            transport = PicoTransport(candidate)
            state = transport.command("STATUS", "DISARMED")
            if transport.protocol != PROTOCOL:
                raise ValueError(f"Версия {transport.protocol}; нужна {PROTOCOL}")
            scope = "SHA256 файлов + " if volume else ""
            return candidate, f"Проверено: {scope}HELLO {transport.protocol}, STATUS {state}; порт {candidate}"
        except (OSError, ValueError) as exc:
            errors.append(f"{candidate}: {exc}")
        finally:
            if transport is not None:
                transport.close()
    raise ValueError("Data port Pico не подтверждён. Переподключите плату без BOOTSEL, "
                     "обновите список и выберите CDC data port. " + "; ".join(errors))
