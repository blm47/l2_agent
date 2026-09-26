import time

import microcontroller
import usb_cdc
import usb_hid
import watchdog
from control import Control

# Reset страхует зависание Python/USB; обычное освобождение работает быстрее.
microcontroller.watchdog.timeout = 2
microcontroller.watchdog.mode = watchdog.WatchDogMode.RESET
keyboard = next(d for d in usb_hid.devices if d.usage_page == 1 and d.usage == 6)
mouse = next(d for d in usb_hid.devices if d.usage_page == 1 and d.usage == 2)
control = Control(keyboard, mouse, time.monotonic)
port = usb_cdc.data
try:
    if port is not None:
        port.timeout = 0
        port.write_timeout = 0.05
        while True:
            control.tick(port.connected)
            if port.connected:
                for reply in control.feed(port.read(64)):
                    if port.write(reply) != len(reply):
                        raise OSError("Неполный ответ USB CDC")
            else:
                port.reset_input_buffer()
            microcontroller.watchdog.feed()
            time.sleep(0.005)
finally:
    control.disarm()
