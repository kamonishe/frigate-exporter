from __future__ import annotations

from pydantic import BaseModel, Field


class ReviewData(BaseModel):
    objects: list[str] = Field(default_factory=list)
    sub_labels: list[str] = Field(default_factory=list)
    zones: list[str] = Field(default_factory=list)
    audio: list[str] = Field(default_factory=list)


class Review(BaseModel):
    """
    Completed Frigate review received from MQTT.
    """

    id: str
    camera: str

    start_time: float
    end_time: float

    severity: str

    thumb_path: str

    data: ReviewData = Field(default_factory=ReviewData)
