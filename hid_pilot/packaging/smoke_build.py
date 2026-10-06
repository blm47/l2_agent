"""
Проверка собранных EXE в отдельной папке с чистыми путями Python.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    destination = ROOT.parent / "artifacts" / "hid-pilot" / "standalone-smoke"
    destination.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    system = Path(environment.get("SystemRoot", "C:/Windows"))
    environment["PATH"] = f"{system / 'System32'};{system}"
    environment["LOCALAPPDATA"] = str(destination / "isolated-user-data")
    reports = {}
    for kind in ("single", "portable"):
        folder = Path(tempfile.mkdtemp(prefix=f"Перенос приложения {kind}-", dir=destination))
        if kind == "single":
            shutil.copyfile(ROOT / "dist" / "HID_Pilot.exe", folder / "HID_Pilot.exe")
            shutil.copytree(ROOT / "dist" / "extensions", folder / "extensions", dirs_exist_ok=True)
            executable = folder / "HID_Pilot.exe"
        else:
            shutil.copytree(ROOT / "dist" / "HID_Pilot_Portable", folder, dirs_exist_ok=True)
            shutil.copytree(ROOT / "dist" / "extensions", folder / "extensions", dirs_exist_ok=True)
            executable = folder / "HID_Pilot.exe"
        report = folder / "self-test.json"
        preview = folder / "firmware-panel.png"
        process = subprocess.run([str(executable), "--self-test", "--report", str(report),
                                  "--gui-preview", str(preview)], cwd=folder,
                                 env=environment, timeout=60, check=False,
                                 creationflags=subprocess.CREATE_NO_WINDOW,
                                 capture_output=True)
        if process.returncode != 0 or not report.exists():
            raise RuntimeError(f"EXE {kind} не прошёл self-test: {process.returncode}; {process.stderr!r}")
        result = json.loads(report.read_text("utf-8"))
        if not result["ok"] or not result["frozen"]:
            raise RuntimeError(f"EXE {kind} не является проверенной автономной сборкой: {result}")
        reports[kind] = result
    (destination / "result.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
