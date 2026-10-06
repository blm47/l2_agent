# Подключаемые расширения HID Pilot

Папка `extensions` находится рядом с `HID_Pilot.exe`. Алгоритмы хила и фарма
не входят в EXE. Python устанавливать отдельно не нужно: расширения выполняет
встроенный runtime приложения. При работе из исходников используется эта же папка.

`commands.json` регистрирует команды DSL. `hid_pilot/` содержит Python-модули
расширений, `scripts/` — примеры `.pilot`. Профили полос и откаты хранятся рядом
с пользовательским `.pilot`. Меню «Файл → Открыть папку расширений» открывает
папку с подключаемыми алгоритмами.

Чтобы обновить хилер, замените `hid_pilot/healer.py`, `healer_runtime.py` и
`healer_observer.py`, затем перезапустите приложение. Пересборка EXE не требуется.
Пользовательские `.pilot` не перезаписываются автоматически. Новую версию примера
можно открыть непосредственно из `extensions/scripts` и сохранить отдельно.

## Добавление команды

В `commands.json` укажите новую команду с числом аргументов, Pydantic-моделью
настроек и ссылками на функции проверки и запуска:

```json
{
  "arguments": 1,
  "config": "hid_pilot.custom:CustomConfig",
  "prepare": "hid_pilot.custom:prepare_extension",
  "run": "hid_pilot.custom:run_extension",
  "exclusive_observer": false
}
```

Модуль `hid_pilot/custom.py` лежит в этой папке. Функции имеют сигнатуры:

```python
def prepare_extension(base: Path, args: tuple[str, ...]) -> None: ...
def run_extension(controller: ActionController, base: Path, args: tuple[str, ...],
                  config: CustomConfig, notify: Callable[[str], None], deadline: float) -> None: ...
```

Проверка выполняется до START и игрового ввода. Запуск получает действующий
ActionController; все игровые команды должны идти через него. Функция обязана
учитывать deadline и checkpoints. `exclusive_observer` отключает отдельный монитор
пати перед запуском собственного наблюдателя.

Расширения — локальный Python-код пользователя, не sandbox. Они используют
зависимости, уже включённые в EXE; добавление новой сторонней библиотеки требует
обновления runtime. Базовые команды `wait/key/move/click/find/color/repeat/if_found`
доступны без этой папки.
