from __future__ import annotations

from app.models.config import FilterConfig
from app.models.review import Review


class ReviewFilter:
    """
    Determines whether a completed Frigate review should be exported.
    """

    def __init__(self, config: FilterConfig) -> None:
        self._cameras = set(config.cameras)
        self._labels = set(config.labels)
        self._severity = set(config.severity)

    def matches(self, review: Review) -> bool:
        if self._cameras and review.camera not in self._cameras:
            return False

        if self._severity and review.severity not in self._severity:
            return False

        if self._labels:
            review_labels = set(review.data.objects)
            review_labels.update(review.data.audio)

            if not review_labels.intersection(self._labels):
                return False

        return True
