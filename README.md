# L2 Agent

Experimental Windows-native AI agent for playing Lineage 2 on the LU4 server through a Parsec client window.

The project is intentionally designed around constrained local hardware:
- Intel Core i7-9700
- NVIDIA GTX 1080 Ti 11 GB
- 16 GB RAM
- Windows

The application supports selecting a Parsec window or a local LU4 window.
Capture and input checks use the selected window's client area.

## Status — 2026-09-25

Current milestone: **M1 — Perception**. M0 was accepted for Parsec; local LU4
skill activation through SendInput remains unconfirmed. A Pico controller is
connected with an optional ActionController backend; the user confirms working control
(see [Pico setup](docs/PICO_SETUP.md)). Bonsai 2 is deployed locally, but is not yet
integrated into the agent. ROI detection/calibration and a minimal WorldState
contract exist. Confirmed bar ROIs now produce approximate fill percentages and
live WorldState in GUI; real partial-bar validation is still pending.
The selected target's name is read asynchronously from its standard LU4 header
when the target HP ROI is configured. Unknown or stale OCR is shown as `?`;
confidence and reasons appear in the WorldState tooltip.
See [roadmap](docs/ROADMAP.md) for acceptance criteria and remaining work.

Для запуска с Pico: `.\.venv\Scripts\python.exe -m l2_agent --pico-port COM4`
(нужен extra `pico`). Клавиши/кнопки идут через USB HID, наведение курсора — через
SendInput. Инструкция и проверки: [Pico setup](docs/PICO_SETUP.md).

## Core idea

```text
Parsec window
    |
    v
Capture -> Perception -> WorldState -> Agent -> Skill -> ActionValidator -> ActionController
                     ^                                      |
                     |                                      v
              Knowledge / Memory                        SendInput
```

The LLM is a planner, not a frame-by-frame controller.

## Current model plan
Primary experiment:
- Bonsai 2 27B ternary quantized model

Fallbacks:
- Qwen3.5-4B
- Qwen3-4B quantized

The final choice will be based on benchmarks on the target GTX 1080 Ti.

## Initial perception plan
- BetterCam for Windows capture
- OpenCV for deterministic UI parsing
- OCR for game text
- small YOLO-family detector for mobs/NPC/UI objects
- multimodal LLM only as a fallback for ambiguous scenes

## Storage
Initial design:
- SQLite
- FAISS only when semantic retrieval becomes necessary
- local trajectory data

No distributed infrastructure is planned for the first milestones.

## Milestones
- M0: Eyes & Hands
- M1: Perception
- M2: LLM advisory mode
- M3: Autonomous closed loop
- M4: Knowledge/RAG
- M5: Episodic memory
- M6: Learning / policy distillation

See docs/ROADMAP.md.

## Development status
The window selector lists all visible top-level windows, including minimized
windows, without filtering by title or executable name.
Each entry shows title, executable, PID and HWND to distinguish multiple clients.
Refresh the list after launching a client. Renamed clients and character-name
titles work without configuration. Windows with unavailable process names remain
listed by title/PID/HWND. Input checks still enforce selected HWND/PID, focus,
client bounds, integrity level and F10.
LU4 input troubleshooting: START now compares Windows integrity levels before
arming. A local check found LU4 at High (12288), while the agent ran at Medium
(8192). If the GUI reports higher game privileges, close the agent, open PowerShell
as administrator, change to this project directory and run
`.\.venv\Scripts\python.exe -m l2_agent`. Then test R and the mouse with the usual
three-second delay. Windows accepting an event does not confirm a game response.
Automatic bar clarification waits while manual input is armed.
Manual input tests also offer top-row digits 1–4 for 100 ms and R for 300 ms.
Timing diagnostics add digits 1–4 at 300/1000 ms, W at 100/1000 ms and both mouse
buttons at 100/300/1000 ms. All use down followed by watchdog-controlled up.
The hold deadline now starts after SendInput returns, so pre-send window checks
do not consume the requested duration. Release logs include elapsed process time;
this measures submission timing, not game-side reception. Compare the same key or
button at different durations before attributing a failure to the input backend.
Choose a digit bound to a visible action in your LU4 settings and compare it with
pressing the same physical key. Each test sends one press with watchdog release;
it is not an automatic sequence. The latest user report narrows working local
input to long holds; short presses/clicks remain unverified after the timing fix.

Local LU4 is also supported: restart the agent, refresh the window list and select
the LU4 window instead of Parsec. Supported process names: `lu4.exe`, `lu4.bin`,
`lu4.bin.exe`. Capture and input use the selected client area with the same F10,
focus and geometry checks. Use windowed mode for initial verification.
After switching between Parsec and LU4, repeat bar search before confirming ROI:
existing profiles are currently matched by frame size, not by game installation.

M0 accepted: preview, validated SendInput, bounded holds, watchdog, global F10,
manual input tests and a completed 600-second preview run (see docs/M0_ACCEPTANCE.md).
Current milestone M1: automatic bar proposals using OpenCV and local OCR, user
confirmation, saved ROI profiles and debug overlays. Numeric bar parsing,
WorldState, general text recognition and object detection remain outstanding.

## Локальный запуск

Окружение разработки: Python 3.12, зависимости фиксируются в `uv.lock`.
В PowerShell из корня проекта:

```powershell
$env:UV_CACHE_DIR = "$PWD\cache\uv"
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.python"
uv sync --locked --extra dev --extra capture --extra perception
.\.venv\Scripts\python.exe -m l2_agent
```

Если окружение уже установлено, достаточно последней команды.
Диагностика без GUI: `.\.venv\Scripts\python.exe -m l2_agent --list-windows`.
Логи: `logs/agent.log`, ротация по 2 MB, три резервных файла.
Запускайте из обычной пользовательской сессии Windows с доступом к рабочему столу.

GUI показывает окна процессов `parsecd.exe` / `parsec.exe`. При нескольких окнах
выберите окно удалённого рабочего стола. Пока наличие окна не подтверждает
подключение к игре. START разрешает только ручные тесты ввода M0.

## Проверка первой итерации

1. Откройте Parsec и GUI агента, нажмите «Обновить список окон».
2. Переместите и измените размер Parsec: координаты и размер должны обновиться.
3. Сверните и восстановите Parsec; проверьте статус окна.
4. Переключите фокус между окнами; проверьте поле «Фокус».
5. Закройте Parsec: GUI должен показать недоступность; после повторного открытия
   обновите список окон.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\ruff.exe format --check src tests
.\.venv\Scripts\mypy.exe src --ignore-missing-imports
```

## Проверка preview

Preview включён при запуске; чекбокс позволяет остановить и возобновить захват.
Видны FPS новых кадров за последнюю секунду и возраст последнего кадра.
На неподвижном рабочем столе BetterCam может не выдавать новые кадры: FPS падает,
а последнее изображение остаётся. При сворачивании и ошибке preview очищается.

Разместите клиентскую область Parsec целиком на одном мониторе и не перекрывайте
её другими окнами: Desktop Duplication захватывает видимую область экрана.
Пересечение нескольких мониторов и частичный выход за экран пока не поддерживаются.
Окно агента лучше разместить рядом с Parsec, чтобы избежать изображения самого preview.
После изменения размера Parsec изображение должно обновиться с сохранением пропорций.

Автоматическая проверка реального захвата и рендера GUI без видимого окна,
с логированием RSS каждые пять секунд (кадры не сохраняются):

```powershell
.\.venv\Scripts\python.exe scripts\smoke_preview.py --seconds 30
# Длительная проверка для приёмки M0:
.\.venv\Scripts\python.exe scripts\smoke_preview.py --seconds 600
```

Скрипт сохраняет `artifacts/preview-smoke.json`; другой путь задаётся `--report`.
В отчёте есть FPS, RSS, private committed memory, максимальная пауза между кадрами
и прирост private memory после прогрева. Для проверки оставьте в Parsec движущуюся
сцену; статичное изображение не подтверждает непрерывность захвата.
Прогон отклоняется при отсутствии кадров, паузе более 3 секунд либо росте средней
private memory более 32 MB между интервалом 60–120 секунд и последней минутой
(для прогонов от 180 секунд). Это диагностические пороги M0, а не доказательство
отсутствия любой утечки. Поле `ten_minute_run` отдельно отмечает длительность;
успешный короткий smoke test не закрывает приёмку M0.

## Ручная проверка ввода M0

1. Перезапустите GUI. Убедитесь, что показано «\ зарегистрирована».
   Клавиша `\` над Enter зарезервирована до закрытия приложения. Если она занята другой
   программой или другим экземпляром агента, START недоступен.
2. Нажмите START. Само нажатие не отправляет события в игру.
3. Выберите «Курсор в центр Parsec», нажмите «Выполнить через 3 секунды» и
   переключитесь в Parsec. Курсор должен переместиться в центр клиентской области.
4. Проверьте «Клавиша R — 100 мс», затем ЛКМ в выбранной вами точке.
   Результат зависит от игровых биндов; тест не интерпретирует действия персонажа.
5. Для проверки остановки выберите «Удержать W — 1000 мс» либо ПКМ, переключитесь
   в Parsec и нажмите `\` во время удержания. В GUI должен появиться EMERGENCY,
   клавиша/кнопка должна отпуститься. Повторный тест требует нового START.
6. Проверьте PAUSE/STOP во время трёхсекундного ожидания: тест должен отмениться.
   Потеря фокуса во время удержания также освобождает ввод. Если отсчёт закончится
   без фокуса Parsec, действие будет отклонено.

Preview управляется отдельно своим чекбоксом и остаётся доступным после STOP.
Разрешены отдельные WASDQERIM, цифры, F1–F8, SPACE, TAB, ESC, левая и правая кнопки.
Клавиатурные нажатия и отпускания отправляются через scan codes, как в проверенном
пользователем примере `tests/TEEEEST.py` (W — 0x11, R — 0x13). Имена обозначают
физические позиции US QWERTY, а не ввод текста в текущей раскладке.
Ctrl/Alt/Shift/Win и комбинации запрещены; физически удерживаемые модификаторы
блокируют новые действия. Только одно удержание одновременно, максимум 1000 мс,
не более 20 новых действий в секунду. Watchdog проверяет удержание каждые 10 мс;
это целевой интервал, а не гарантия real-time Windows.

Освобождаются только клавиши/кнопки, отправленные агентом. Ошибка SendInput блокирует
дальнейший ввод; неотправленные отпускания остаются на повторную отправку. Если
в логах сохраняются ошибки освобождения, не продолжайте ручные тесты до устранения
причины. Parsec и агент должны запускаться с одинаковым уровнем прав; SendInput
не обходит ограничения Windows/UIPI. Между проверкой фокуса и доставкой события
Windows остаётся небольшое окно гонки, поэтому автономный режим ещё не включён.

## M1: автоматический поиск полос

Поиск включён по умолчанию. OpenCV находит группы цветных полос, а локальная модель
PP-OCRv4 через RapidOCR проверяет подписи HP/MP/CP рядом с ними. Модель работает
на CPU в фоновом потоке; кадры не отправляются во внешние сервисы.

1. Когда игра доступна в Parsec, дождитесь свежего preview и предложения с рамками.
2. Проверьте назначение и **полную длину** полос, включая пустую часть. Первый вариант
   всегда требует подтверждения: оценка согласованности признаков не является
   вероятностью правильного распознавания. Неполные полосы могут дать короткие рамки.
3. Подтвердите кнопкой «Да, это мои полосы и их полная длина» или выберите
   «Нет, другой вариант». Рисовать прямоугольники для этого не требуется.
4. Для HP цели можно выбрать дополнительный вариант в списке и проверить рамку.
   По умолчанию цель остаётся неизвестной: отдельная красная полоса может быть чужой.
5. Подтверждённый профиль сохраняется в `config/roi.local.json` и используется повторно.

При отсутствии кандидатов поиск повторяется не чаще раза в 15 секунд. После отказа
от всех вариантов повторите поиск кнопкой, когда интерфейс станет лучше виден.
При изменении размера Parsec нужен новый поиск. Если передвинули панели внутри игры
или изменили масштаб UI без изменения окна, запустите повторный поиск вручную.
Подтверждение сохраняет координаты, но не обучает модель. Редактор ниже остаётся
необязательным запасным инструментом.

Проверено: синтетическая панель с тремя подписями и отрицательный пример рабочего
стола. На небольшом синтетическом кадре первый запуск занял около 1 секунды,
повторный — 0,11 секунды; это не benchmark игрового экрана. Проверка на настоящем
интерфейсе игры ожидает её доступности. Проценты HP/MP/CP пока не вычисляются.

## M1: необязательная ручная настройка областей

1. Откройте игру в Parsec и дождитесь свежего preview.
2. Откройте ручную настройку полос. Ручной ввод остановится,
   появится редактор со стоп-кадром. Выделения мышью не отправляются в игру.
3. Выберите HP и протяните прямоугольник по всей внутренней длине полосы,
   включая незаполненную часть. Не захватывайте рамку и соседние полосы.
4. Повторите для MP, CP и HP цели. Если цели на экране нет, её область можно
   оставить ненастроенной. Кнопка удаления убирает только выбранную область.
5. Нажмите Save. Рамки появятся поверх live preview; профиль сохраняется в
   `config/roi.local.json` и загружается при следующем запуске.

Профиль хранит нормализованные координаты и размер кадра калибровки. При изменении
размера клиентской области рамки отключаются до новой настройки: интерфейс игры
может перестроиться, поэтому простое масштабирование ненадёжно. Если передвинули
панели внутри игры или поменяли масштаб UI, также разметьте их заново.
Cancel сохраняет предыдущие настройки. Повреждённый профиль выводит ошибку,
а сохранение новых настроек заменяет файл только после успешной записи.

Эта итерация только задаёт области наблюдения. Проценты HP/MP/CP пока не вычисляются;
следующий шаг M1 — детерминированный парсер полос и проверка на размеченных кадрах.
## Сбор кадров для детектора

Кнопка «Сохранить кадр для разметки через 3 секунды» сохраняет исходный кадр
и метаданные локально в `datasets/detector/raw/`. После нажатия переключитесь
в игру. Формат и порядок разметки: [DETECTOR_DATASET.md](docs/DETECTOR_DATASET.md).
Известные отложенные ограничения: [TODO.md](docs/TODO.md).
