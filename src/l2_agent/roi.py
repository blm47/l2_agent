import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BarName = Literal["hp", "mp", "cp", "target_hp"]
BAR_LABELS: dict[BarName, str] = {"hp": "HP", "mp": "MP", "cp": "CP", "target_hp": "HP цели"}
DEFAULT_ROI_PATH = Path("config/roi.local.json")


class NormalizedBox(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)
    x2: float = Field(ge=0, le=1)
    y2: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_area(self) -> "NormalizedBox":
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("Область должна иметь положительную ширину и высоту")
        return self

    def pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        if width <= 0 or height <= 0:
            raise ValueError("Размер кадра должен быть положительным")
        return (
            math.floor(self.x1 * width),
            math.floor(self.y1 * height),
            min(width, math.ceil(self.x2 * width)),
            min(height, math.ceil(self.y2 * height)),
        )


class RoiProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    frame_width: int = Field(gt=0)
    frame_height: int = Field(gt=0)
    bars: dict[BarName, NormalizedBox] = Field(default_factory=dict)

    def matches(self, width: int, height: int) -> bool:
        return (self.frame_width, self.frame_height) == (width, height)


def load_profile(path: Path = DEFAULT_ROI_PATH) -> RoiProfile | None:
    if not path.exists():
        return None
    return RoiProfile.model_validate_json(path.read_text(encoding="utf-8"))


def save_profile(profile: RoiProfile, path: Path = DEFAULT_ROI_PATH) -> None:
    # Сначала проверяем и записываем временный файл, затем заменяем текущий профиль.
    profile = RoiProfile.model_validate_json(profile.model_dump_json())
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)
