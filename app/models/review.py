from __future__ import annotations

from pydantic import BaseModel


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
