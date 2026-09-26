# Pico — настройка и интеграция 2026-09-25

Плата определена по фотографии и boot_out.txt: Raspberry Pi Pico, RP2040,
Board ID `raspberry_pi_pico`, UID `5303284718697D9C`.

Установлена официальная CircuitPython 10.3.1:
https://downloads.circuitpython.org/bin/raspberry_pi_pico/en_US/adafruit-circuitpython-raspberry_pi_pico-en_US-10.3.1.uf2

SHA256 скачанного файла:
`1E3134DE1F2EA2BD1D46019E90781FA5BD95EA518B6665717CB256A9327A563A`.
Это локально вычисленный hash для воспроизводимости, не независимая подпись.
Копия UF2 и исходного code.py находятся в игнорируемой папке `artifacts/pico/`.

## Текущая конфигурация

- `G:` — диск CIRCUITPY.
- COM3 — сервисная консоль CircuitPython.
- COM4 — отдельный CDC data port, нужен DTR при открытии.
- USB HID keyboard и mouse определяются Windows без ошибок.
- MIDI отключён, чтобы освободить USB endpoints.

Буквы дисков и COM-порты могут измениться при переподключении.
На плату скопированы `boot.py`, `code.py`, `control.py` из `firmware/pico/`.
Изменение boot.py требует аппаратного reset или переподключения USB без BOOTSEL.
Остальные файлы запускаются через auto-reload после записи.

## Запуск агента

```powershell
uv sync --extra capture --extra perception --extra pico --extra dev
.\.venv\Scripts\python.exe -m l2_agent --pico-port COM4
```

В GUI показано `Pico HID + SendInput cursor`. Клавиши и кнопки мыши отправляет
Pico; точное наведение курсора остаётся через SendInput с проверками координат.
Без `--pico-port` сохраняется прежний режим SendInput. Ошибка Pico не переключает
нажатия на SendInput. После ошибки канала требуется перезапуск приложения.

Выберите нужное окно, проверьте статус `\`, нажмите START. Выберите тест R на 100 мс,
запустите его с задержкой и переведите фокус в игру. Для проверки остановки используйте
W на 1000 мс и `\` во время удержания. STOP/PAUSE и потеря фокуса освобождают ввод.
Проверка уровня прав Windows сохраняется и для этого режима.

## Защита ввода

- Все игровые команды проходят ActionValidator и ActionController.
- На плате отдельный allowlist без модификаторов, `\`, F10 и OS shortcuts.
- Одно удержание, длительность 1–1000 мс, deadline платы не продлевается heartbeat.
- Heartbeat каждые 100 мс; без него плата освобождает ввод и снимает ARM через 350 мс.
- Закрытие CDC/DTR снимает ARM. Аппаратный watchdog перезагружает плату при зависании
  цикла более двух секунд. Эти интервалы не гарантируют real-time USB/Windows.
- STOP освобождает оба HID-устройства и требует нового START.
- Номер команды сверяется в ACK; timeout/неверный ответ закрывает COM без повтора.
- Входная строка ограничена 64 байтами; ошибка протокола снимает ARM.

## Протокол L2_PICO_V1

ASCII-строки: `<sequence> <command>\n`, ответ `<sequence> OK <result>\n`.
Sequence возрастает в пределах CDC-сессии. Поддержаны HELLO, STATUS, ARM, HB,
KEY `<name> <ms>`, BUTTON `<left|right> <ms>`, RELEASE, STOP.
Начальное состояние DISARMED. Протокол предназначен для ActionController,
не для прямого управления из LLM или GUI.

## Проверено

Unit-тесты покрывают deadline, потерю heartbeat/соединения, ошибочные команды,
allowlist, переполнение, повтор sequence, STOP, ошибки ACK и маршрутизацию через
ActionController. На реальном Pico проверены HELLO, ARM/STATUS/STOP, автоматический
DISARMED без heartbeat и повторное подключение. GUI smoke с COM4 прошёл:
F10 зарегистрирована, контроллер не вооружён, закрытие корректно.

2026-09-25 23:06: выполнен реальный тест R на 300 мс через Pico/ActionController
в LU4 (HWND 9182214, PID 30312), оба процесса с integrity 12288. На кадре после
теста видны начало каста и новая строка `You use Wind Strike`, отсутствующая до
теста. Windows подтвердила R down и последующее отпускание. Это подтверждает
активацию навыка в данном тесте, но не его завершение/урон или остальные кнопки.
Артефакты: `artifacts/pico/native-probe/{before.png,after.png,result.json}`.
Повторяемый сценарий: `scripts/pico_native_probe.py --hwnd ... --pid ... --port COM4`.

Реальная проверка `\` во время удержания и кнопок мыши остаётся открытой.
Автономный режим не включён. Скрытый GUI с Pico был штатно закрыт перед тестом,
чтобы освободить COM4 и глобальную горячую клавишу.

## История первичной диагностики

Команды ASCII завершаются LF; CRLF также поддерживается:

| Запрос | Ответ |
| --- | --- |
| PING | PONG L2_PICO_DIAG_V1 |
| STATUS | OK DIAGNOSTIC_ONLY INPUT_DISABLED |
| INVALID | ERR UNKNOWN_COMMAND |

Длина строки ограничена 64 байтами. Переполнение отбрасывает строку до LF.
Частичная команда сбрасывается при обнаружении закрытия data port.

Эти ответы относятся к первоначальной диагностической прошивке; текущая прошивка
использует L2_PICO_V1 выше. `diagnostics.py` оставлен как исторический тестовый модуль.

Документация: https://circuitpython.org/board/raspberry_pi_pico/
и https://docs.circuitpython.org/en/stable/shared-bindings/usb_cdc/index.html.
