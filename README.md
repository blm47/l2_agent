# L2 Agent

Experimental Windows-native AI agent for playing Lineage 2 on the LU4 server through a Parsec client window.

The project is intentionally designed around constrained local hardware:
- Intel Core i7-9700
- NVIDIA GTX 1080 Ti 11 GB
- 16 GB RAM
- Windows

The remote game client runs elsewhere. L2 Agent sees and controls only the local Parsec window.

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
M0, iteration 3: preview, validated SendInput primitives, bounded holds, an independent
input watchdog, global F10 hard-stop and manual GUI tests. M0 acceptance criteria
are not yet satisfied: input tests were confirmed by the user; the long preview test remains.

## Локальный запуск

Окружение разработки: Python 3.12, зависимости фиксируются в `uv.lock`.
В PowerShell из корня проекта:

```powershell
$env:UV_CACHE_DIR = "$PWD\cache\uv"
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.python"
uv sync --locked --extra dev --extra capture
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

1. Перезапустите GUI. Убедитесь, что показано «F10 зарегистрирована».
   F10 зарезервирована приложением до его закрытия. Если клавиша занята другой
   программой или другим экземпляром агента, START недоступен.
2. Нажмите START. Само нажатие не отправляет события в игру.
3. Выберите «Курсор в центр Parsec», нажмите «Выполнить через 3 секунды» и
   переключитесь в Parsec. Курсор должен переместиться в центр клиентской области.
4. Проверьте «Клавиша R — 100 мс», затем ЛКМ в выбранной вами точке.
   Результат зависит от игровых биндов; тест не интерпретирует действия персонажа.
5. Для проверки остановки выберите «Удержать W — 1000 мс» либо ПКМ, переключитесь
   в Parsec и нажмите F10 во время удержания. В GUI должен появиться EMERGENCY,
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

Реальные тесты ввода подтверждены пользователем. Следующий шаг M0 — пройти
десятиминутную проверку preview. До приёмки M0 перехода к M1 нет.
