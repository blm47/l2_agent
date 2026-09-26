import os
import time
import uuid
import random

import win32gui
import win32ui
import win32con
from PIL import Image


OUTPUT_DIR = "screenshots"

MIN_INTERVAL = 5
MAX_INTERVAL = 20

WINDOW_TITLE_PART = "LU4"


def find_lu4_window():
    """Ищет видимое окно, содержащее LU4 в заголовке."""
    found = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return

        title = win32gui.GetWindowText(hwnd)

        if WINDOW_TITLE_PART.lower() in title.lower():
            found.append((hwnd, title))

    win32gui.EnumWindows(callback, None)

    if not found:
        return None, None

    # Берём первое найденное окно
    return found[0]


def capture_client_area(hwnd):
    """
    Делает скриншот клиентской области окна.
    Используется BitBlt, поэтому окно должно нормально отрисовываться.
    """

    left, top = win32gui.ClientToScreen(hwnd, (0, 0))

    client_rect = win32gui.GetClientRect(hwnd)
    width = client_rect[2] - client_rect[0]
    height = client_rect[3] - client_rect[1]

    desktop_hwnd = win32gui.GetDesktopWindow()
    desktop_dc = win32gui.GetWindowDC(desktop_hwnd)

    src_dc = win32ui.CreateDCFromHandle(desktop_dc)
    mem_dc = src_dc.CreateCompatibleDC()

    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(src_dc, width, height)

    mem_dc.SelectObject(bitmap)

    mem_dc.BitBlt(
        (0, 0),
        (width, height),
        src_dc,
        (left, top),
        win32con.SRCCOPY,
    )

    bmp_info = bitmap.GetInfo()
    bmp_bits = bitmap.GetBitmapBits(True)

    image = Image.frombuffer(
        "RGB",
        (bmp_info["bmWidth"], bmp_info["bmHeight"]),
        bmp_bits,
        "raw",
        "BGRX",
        0,
        1,
    )

    win32gui.DeleteObject(bitmap.GetHandle())
    mem_dc.DeleteDC()
    src_dc.DeleteDC()
    win32gui.ReleaseDC(desktop_hwnd, desktop_dc)

    return image


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Ищу окно LU4...")

    hwnd, title = find_lu4_window()

    if hwnd is None:
        print("Окно LU4 не найдено.")
        return

    print(f"Найдено окно:")
    print(f"  hwnd  = {hwnd}")
    print(f"  title = {title}")
    print()
    print(
        f"Сохраняю скриншоты каждые "
        f"{MIN_INTERVAL}-{MAX_INTERVAL} секунд."
    )
    print("Ctrl+C для остановки.")
    print()
    time.sleep(3)

    try:
        while True:

            # Если окно было закрыто
            if not win32gui.IsWindow(hwnd):
                print("Окно LU4 закрыто.")
                break

            image = capture_client_area(hwnd)

            image_id = uuid.uuid4().hex

            filename = os.path.join(
                OUTPUT_DIR,
                f"{image_id}.png"
            )

            image.save(filename)

            print(
                f"[saved] {image_id}.png "
                f"({image.width}x{image.height})"
            )

            delay = random.uniform(
                MIN_INTERVAL,
                MAX_INTERVAL
            )

            print(f"Следующий кадр через {delay:.1f} сек.")

            time.sleep(delay)

    except KeyboardInterrupt:
        print("\nОстановлено.")


if __name__ == "__main__":
    main()