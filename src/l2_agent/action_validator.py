from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from l2_agent.geometry import ClientRect, NormalizedPoint
from l2_agent.windows import WindowSnapshot

ALLOWED_KEYS = {
    **{key: ord(key) for key in "WASDQERIM1234567890"},
    **{f"F{index}": 0x6F + index for index in range(1, 9)},
    "SPACE": 0x20,
    "TAB": 0x09,
    "ESC": 0x1B,
}
MOUSE_BUTTONS = {"left": (0x0002, 0x0004, 0x01), "right": (0x0008, 0x0010, 0x02)}


class ActionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    kind: Literal["key_down", "mouse_down", "move"]
    key: str | None = None
    button: str | None = None
    point: NormalizedPoint | None = None
    duration_ms: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_arguments(self) -> "ActionRequest":
        if self.kind == "key_down":
            if self.key not in ALLOWED_KEYS or self.button is not None or self.point is not None:
                raise ValueError("Клавиша или комбинация запрещена")
        elif self.kind == "mouse_down":
            if self.button not in MOUSE_BUTTONS or self.key is not None or self.point is not None:
                raise ValueError("Кнопка мыши запрещена")
        elif self.point is None or self.key is not None or self.button is not None:
            raise ValueError("Для перемещения нужна нормализованная точка")
        return self


class ActionValidator:
    def validate(
        self,
        snapshot: WindowSnapshot,
        *,
        armed: bool,
        hard_stop_ready: bool,
        modifiers_pressed: bool,
        desktop: ClientRect,
    ) -> ClientRect:
        if not armed or not hard_stop_ready:
            raise ValueError("Ввод остановлен или hard-stop недоступен")
        if not snapshot.focused or snapshot.minimized or snapshot.rect is None:
            raise ValueError(
                "Выбранное окно Lineage 2 / LU4 / Parsec должно быть видимым и в фокусе"
            )
        if modifiers_pressed:
            raise ValueError("Отпустите Ctrl, Alt, Shift и Win перед вводом")
        rect = snapshot.rect
        if not (
            desktop.contains(rect.left, rect.top)
            and desktop.contains(rect.right - 1, rect.bottom - 1)
        ):
            raise ValueError(
                "Клиентская область Lineage 2 / LU4 / Parsec выходит за пределы экрана"
            )
        return rect
