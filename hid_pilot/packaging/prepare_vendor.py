"""
Загрузка закреплённых UF2 из официальных источников для автономной сборки.
"""

import hashlib
import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hid_pilot.firmware_manager import validate_uf2
from hid_pilot.logging_setup import setup_logging

VERSION = "10.3.1"
SOURCES = {
    "raspberry_pi_pico.uf2": f"https://downloads.circuitpython.org/bin/raspberry_pi_pico/en_US/adafruit-circuitpython-raspberry_pi_pico-en_US-{VERSION}.uf2",
    "raspberry_pi_pico_w.uf2": f"https://downloads.circuitpython.org/bin/raspberry_pi_pico_w/en_US/adafruit-circuitpython-raspberry_pi_pico_w-en_US-{VERSION}.uf2",
    "flash_nuke.uf2": "https://datasheets.raspberrypi.com/soft/flash_nuke.uf2",
}


def main() -> int:
    logger = setup_logging()
    directory = Path(__file__).resolve().parents[1] / "firmware" / "vendor"
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else {
        "circuitpython_version": VERSION, "assets": {}}
    for name, url in SOURCES.items():
        path = directory / name
        entry = manifest["assets"].get(name)
        if not path.exists():
            logger.info("Загрузка встроенной прошивки: %s", url)
            request = urllib.request.Request(url, headers={"User-Agent": "HIDPilot-build/0.2"})
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read(4_000_001)
            if len(data) > 4_000_000:
                raise ValueError("UF2 превышает лимит размера")
            path.write_bytes(data)
        validate_uf2(path, multi_family=name == "flash_nuke.uf2")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        if entry is not None and entry["sha256"] != checksum:
            raise ValueError(f"Checksum {name} отличается от закреплённого manifest")
        if entry is None:
            manifest["assets"][name] = {"url": url, "sha256": checksum,
                                       "size": path.stat().st_size,
                                       "retrieved_at": datetime.now(UTC).isoformat()}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    for name, url in {
        "CircuitPython-LICENSE.txt": f"https://raw.githubusercontent.com/adafruit/circuitpython/{VERSION}/LICENSE",
        "RaspberryPi-LICENSE.txt": "https://raw.githubusercontent.com/raspberrypi/pico-examples/master/LICENSE.TXT",
    }.items():
        path = directory / name
        if not path.exists():
            with urllib.request.urlopen(url, timeout=60) as response:
                path.write_bytes(response.read(200_000))
    logger.info("UF2 проверены и готовы к автономной сборке")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
