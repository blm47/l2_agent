from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BarObservation(BaseModel):
    """
    Доля заполнения полосы и качество наблюдения, а не абсолютные игровые очки.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    value: float | None = Field(default=None, ge=0, le=1)
    confidence: float = Field(default=0, ge=0, le=1)
    reason: str | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> "BarObservation":
        if self.value is None and self.confidence != 0:
            raise ValueError("Неизвестное значение должно иметь нулевую confidence")
        if self.value is not None and self.confidence == 0:
            raise ValueError("Известное значение требует положительной confidence")
        return self


class PlayerState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hp: BarObservation = Field(default_factory=BarObservation)
    mp: BarObservation = Field(default_factory=BarObservation)
    cp: BarObservation = Field(default_factory=BarObservation)


class NameObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    value: str | None = Field(default=None, min_length=1, max_length=80)
    confidence: float = Field(default=0, ge=0, le=1)
    frame_id: int | None = Field(default=None, ge=0, strict=True)
    timestamp: float | None = Field(default=None, ge=0)
    reason: str | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> "NameObservation":
        if self.value is None and self.confidence != 0:
            raise ValueError("Неизвестное имя должно иметь нулевую confidence")
        if self.value is not None and (
            not self.value.strip()
            or self.confidence == 0
            or self.frame_id is None
            or self.timestamp is None
        ):
            raise ValueError("Распознанное имя требует confidence и метаданных исходного кадра")
        return self


class WorldState(BaseModel):
    """
    Минимальный снимок M1; timestamp — monotonic-время исходного кадра в секундах.

    Неизмеренные полосы неизвестны. HP цели не доказывает её имя или тип.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    version: Literal[1] = 1
    frame_id: int = Field(ge=0, strict=True)
    timestamp: float = Field(ge=0)
    player: PlayerState = Field(default_factory=PlayerState)
    target_hp: BarObservation = Field(default_factory=BarObservation)
    target_name: NameObservation = Field(default_factory=NameObservation)
