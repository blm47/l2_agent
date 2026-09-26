import usb_cdc
import usb_hid
import usb_midi

# Освобождаем USB endpoints для отдельного канала диагностики.
usb_midi.disable()
usb_cdc.enable(console=True, data=True)
usb_hid.enable((usb_hid.Device.KEYBOARD, usb_hid.Device.MOUSE))
