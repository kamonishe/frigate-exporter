import tempfile
import unittest
from pathlib import Path

from app.models.review import Review
from app.services.review_store import ReviewStore


def review(review_id: str = "r1") -> Review:
    return Review(id=review_id, camera="cam", start_time=1, end_time=2, severity="alert", thumb_path="/tmp/thumb.jpg")


class ReviewStoreTests(unittest.TestCase):
    def test_lifecycle_and_duplicate_suppression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            store.initialize()
            item = review()
            self.assertTrue(store.queue_if_needed(item))
            self.assertTrue(store.mark_queued(item.id))
            self.assertFalse(store.queue_if_needed(item))
            store.claim(item.id)
            store.complete(item.id)
            self.assertEqual(store.summary(), {"completed": 1})
            self.assertFalse(store.queue_if_needed(item))

    def test_processing_is_requeued_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            store.initialize()
            item = review()
            store.queue_if_needed(item)
            store.mark_queued(item.id)
            store.claim(item.id)
            store.initialize()
            self.assertEqual(store.due_reviews()[0].id, item.id)

    def test_failed_review_becomes_due_after_retry_delay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            store.initialize()
            item = review()
            store.queue_if_needed(item)
            store.mark_queued(item.id)
            store.fail(item.id, "temporary", retry_after=0)
            self.assertEqual(store.due_reviews()[0].id, item.id)


if __name__ == "__main__":
    unittest.main()
