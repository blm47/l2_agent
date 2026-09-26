# Панель цели LU4

`self.png`: crop [1080, 0, 1490, 115] из локального кадра
`artifacts/ocr-debug/frame-3.png`, 2026-09-25. Выбрана LenaBerkova: имя есть,
красной HP-полосы нет. `gremlin.png`: та же область из
`artifacts/pico/native-probe/before.png`, имя Gremlin и полная красная полоса.

Шаблон `src/l2_agent/assets/lu4-target-close.png` взят из первого кадра:
[1397, 15, 1418, 35]. Он проверяет наличие стандартной панели независимо от HP.
Изменённая тема интерфейса может потребовать другого шаблона.
