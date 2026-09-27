import unittest

from app.models.config import FilterConfig
from app.models.review import Review
from app.services.review_filter import ReviewFilter


def make_review(
    *,
    camera: str = "front",
    severity: str = "alert",
    objects: list[str] | None = None,
    audio: list[str] | None = None,
) -> Review:
    return Review.model_validate(
        {
            "id": "test-review",
            "camera": camera,
            "start_time": 100.0,
            "end_time": 101.0,
            "severity": severity,
            "thumb_path": "/media/frigate/thumb.webp",
            "data": {
                "objects": objects or [],
                "sub_labels": [],
                "zones": [],
                "audio": audio or [],
            },
        }
    )


class ReviewFilterTests(unittest.TestCase):
    def test_matching_review_is_accepted(self) -> None:
        config = FilterConfig(
            cameras=["front"],
            labels=["person"],
            severity=["alert"],
        )

        self.assertTrue(
            ReviewFilter(config).matches(
                make_review(objects=["person"])
            )
        )

    def test_camera_filter(self) -> None:
        config = FilterConfig(
            cameras=["front"],
            labels=[],
            severity=[],
        )

        self.assertFalse(
            ReviewFilter(config).matches(
                make_review(camera="back")
            )
        )

    def test_severity_filter(self) -> None:
        config = FilterConfig(
            cameras=[],
            labels=[],
            severity=["alert"],
        )

        self.assertFalse(
            ReviewFilter(config).matches(
                make_review(severity="detection")
            )
        )

    def test_label_filter_uses_objects_and_audio(self) -> None:
        config = FilterConfig(
            cameras=[],
            labels=["speech"],
            severity=[],
        )

        review_filter = ReviewFilter(config)

        self.assertTrue(
            review_filter.matches(
                make_review(audio=["speech"])
            )
        )

        self.assertFalse(
            review_filter.matches(
                make_review(objects=["car"])
            )
        )

    def test_empty_filters_allow_all(self) -> None:
        config = FilterConfig(
            cameras=[],
            labels=[],
            severity=[],
        )

        self.assertTrue(
            ReviewFilter(config).matches(
                make_review(
                    camera="back",
                    severity="detection",
                    objects=["car"],
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
