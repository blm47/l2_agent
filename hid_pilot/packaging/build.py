"""
Воспроизводимая Windows-сборка одного EXE и переносимой папки с лицензиями.
"""

import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def collect_licenses() -> None:
    destination = ROOT / "packaging" / "licenses"
    destination.mkdir(parents=True, exist_ok=True)
    lines = ["HID Pilot 0.3.1 — bundled third-party components", "",
             "CircuitPython: https://github.com/adafruit/circuitpython/tree/10.3.1",
             "RP2040 flash nuke: https://github.com/raspberrypi/pico-examples/tree/master/flash/nuke",
             "Qt / PySide6: LGPLv3 / GPLv3 / commercial; license files are included.",
             "Qt sources and replacement libraries: https://download.qt.io/archive/qt/6.11/6.11.2/",
             "PySide6 sources: https://code.qt.io/cgit/pyside/pyside-setup.git/ (tag v6.11.2)",
             "The portable folder supports replacement of Qt DLLs; all bundled DLLs are unmodified.", ""]
    skipped = {"pytest", "pyinstaller", "pyinstaller-hooks-contrib", "pefile", "altgraph",
               "iniconfig", "pluggy", "pygments", "setuptools", "pywin32-ctypes"}
    for dist in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata["Name"]):
        name = dist.metadata["Name"]
        if name.lower() in skipped:
            continue
        lines.append(f"{name} {dist.version}")
        for file in dist.files or []:
            if any(word in str(file).lower() for word in ("license", "copying", "notice")):
                source = Path(dist.locate_file(file))
                if source.is_file() and source.stat().st_size <= 1_000_000:
                    target = destination / name / str(file).replace("..", "_")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
    for file in (ROOT / "firmware" / "vendor").glob("*LICENSE.txt"):
        shutil.copyfile(file, destination / file.name)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.exists():
        shutil.copyfile(python_license, destination / "Python-LICENSE.txt")
    (ROOT / "packaging" / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(lines), "utf-8")


def main() -> int:
    if sys.platform != "win32":
        raise RuntimeError("Сборка Windows EXE выполняется на Windows")
    subprocess.run([sys.executable, str(ROOT / "packaging" / "prepare_vendor.py")], check=True)
    collect_licenses()
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                    "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
                    str(ROOT / "packaging" / "HID_Pilot.spec")], check=True)
    dist = ROOT / "dist"
    shutil.copyfile(ROOT / "packaging" / "QUICKSTART.txt", dist / "QUICKSTART.txt")
    shutil.copyfile(ROOT / "packaging" / "THIRD_PARTY_NOTICES.txt", dist / "THIRD_PARTY_NOTICES.txt")
    portable = dist / "HID_Pilot_Portable"
    shutil.copytree(ROOT / "extensions", dist / "extensions", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(dist / "extensions", portable / "extensions", dirs_exist_ok=True)
    shutil.make_archive(str(dist / "HID_Pilot_Extensions-0.3.1"), "zip", dist, "extensions")
    shutil.copyfile(ROOT / "packaging" / "QUICKSTART.txt", portable / "QUICKSTART.txt")
    shutil.make_archive(str(dist / "HID_Pilot_Portable-0.3.1"), "zip", dist, portable.name)
    full = dist / "HID_Pilot_Full"
    full.mkdir(parents=True, exist_ok=True)
    for name in ("HID_Pilot.exe", "QUICKSTART.txt", "THIRD_PARTY_NOTICES.txt"):
        shutil.copyfile(dist / name, full / name)
    shutil.copytree(dist / "extensions", full / "extensions", dirs_exist_ok=True)
    for name in ("README.md", "HEALER.md", "TRAIN_HEALER.md", "DECISIONS.md", "RELEASE_NOTES.md"):
        shutil.copyfile(ROOT / name, full / name)
    shutil.copytree(ROOT / "packaging" / "licenses", full / "licenses", dirs_exist_ok=True)
    shutil.copytree(ROOT / "firmware", full / "firmware", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.make_archive(str(dist / "HID_Pilot_Full-0.3.1"), "zip", dist, full.name)
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in (dist / "HID_Pilot.exe", dist / "HID_Pilot_Portable-0.3.1.zip",
                           dist / "HID_Pilot_Extensions-0.3.1.zip", dist / "HID_Pilot_Full-0.3.1.zip")}
    (dist / "SHA256.json").write_text(json.dumps(hashes, indent=2), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
