import sys
from pathlib import Path

# Алгоритмы подключаются из внешней папки, также у собранного EXE.
_application_root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
__path__.insert(0, str(_application_root / "extensions" / "hid_pilot"))
