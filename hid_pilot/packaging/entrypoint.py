"""
Точка входа Windows-сборки без зависимости от установленного Python.
"""

import sys

if __name__ == "__main__":
    if sys.argv[1:2] == ["--activate-window"]:
        # Helper не загружает Qt/OCR и не создаёт второе окно приложения.
        from hid_pilot.window_activation import helper_main

        raise SystemExit(helper_main(sys.argv[1:]))
    else:
        from hid_pilot.__main__ import main

        raise SystemExit(main())
