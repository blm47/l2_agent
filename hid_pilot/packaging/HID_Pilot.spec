# Все пути ресурсов одинаковы в исходниках, onefile и переносимой папке.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).parent
ocr_datas, ocr_binaries, ocr_hidden = collect_all("rapidocr_onnxruntime")
ort_datas, ort_binaries, ort_hidden = collect_all("onnxruntime")
datas = ocr_datas + ort_datas + copy_metadata("rapidocr-onnxruntime")
datas += [(str(root / "firmware" / name), "firmware")
          for name in ("boot.py", "code.py", "control.py")]
datas += [(str(root / "firmware" / "vendor"), "firmware/vendor"),
          (str(root / "README.md"), "docs"),
          (str(root / "packaging" / "QUICKSTART.txt"), "docs"),
          (str(root / "packaging" / "THIRD_PARTY_NOTICES.txt"), "docs"),
          (str(root / "packaging" / "licenses"), "docs/licenses")]

analysis = Analysis(
    [str(root / "packaging" / "entrypoint.py")],
    pathex=[str(root)],
    binaries=ocr_binaries + ort_binaries,
    datas=datas,
    # Эти модули импортируются внешними алгоритмами и не видны обычному анализу EXE.
    hiddenimports=ocr_hidden + ort_hidden + [
        "serial.tools.list_ports_windows", "hid_pilot.monster_catalogue",
        "hid_pilot.resurrection", "hid_pilot.system_chat", "hid_pilot.party_class",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "torch", "tensorflow", "pytest", "IPython",
              "hid_pilot.farm", "hid_pilot.healer", "hid_pilot.healer_runtime",
              "hid_pilot.healer_observer"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
single = EXE(
    pyz, analysis.scripts, analysis.binaries, analysis.datas, [],
    name="HID_Pilot", debug=False, strip=False, upx=False, console=False,
    version=str(root / "packaging" / "version_info.txt"),
)
portable_exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True,
    # Отдельное имя исключает коллизию PKG cache с однокомпонентным EXE.
    name="HID_Pilot_Portable", debug=False, strip=False, upx=False, console=False,
    version=str(root / "packaging" / "version_info.txt"),
)
portable = COLLECT(
    portable_exe, analysis.binaries, analysis.datas,
    strip=False, upx=False, name="HID_Pilot_Portable",
)
