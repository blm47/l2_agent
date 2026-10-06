"""
Точка входа Windows-сборки без зависимости от установленного Python.
"""

from hid_pilot.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
