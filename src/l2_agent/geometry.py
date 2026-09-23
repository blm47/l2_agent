from pydantic import BaseModel, ConfigDict, Field, model_validator


class NormalizedPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class ClientRect(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    left: int
    top: int
    right: int
    bottom: int

    @model_validator(mode="after")
    def validate_size(self) -> "ClientRect":
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("Клиентская область должна иметь положительный размер")
        return self

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def to_screen(self, point: NormalizedPoint) -> tuple[int, int]:
        # Правая и нижняя границы Win32 не входят в прямоугольник.
        return (
            self.left + round(point.x * (self.width - 1)),
            self.top + round(point.y * (self.height - 1)),
        )

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom
