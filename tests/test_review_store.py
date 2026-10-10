import gc
import os
import tempfile
import time
import unittest
from pathlib import Path

from app.models.review import Review
from app.services.review_store import ReviewStore


def review(review_id: str = "r1") -> Review:
    return Review(id=review_id, camera="cam", start_time=1, end_time=2, severity="alert", thumb_path="/tmp/thumb.jpg")


class ReviewStoreTests(unittest.TestCase):
    @unittest.skipUnless(Path("/proc/self/fd").exists(), "requires Linux descriptor accounting")
    def test_burst_closes_connections_without_garbage_collection(self) -> None:
        gc.collect()
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            with tempfile.TemporaryDirectory() as directory:
                baseline = len(os.listdir("/proc/self/fd"))
                store = ReviewStore(Path(directory))
                store.initialize()
                for index in range(1100):
                    item = review(str(index))
                    self.assertTrue(store.queue_if_needed(item))
                    self.assertTrue(store.mark_queued(item.id))
                    self.assertFalse(store.queue_if_needed(item))
                    store.claim(item.id)
                    store.fail(item.id, "retry", retry_after=0)
                    store.due_reviews()
                    store.complete(item.id)
                    store.summary()
                self.assertEqual(store.summary(), {"completed": 1100})
                self.assertLessEqual(len(os.listdir("/proc/self/fd")), baseline + 2)
        finally:
            if was_enabled:
                gc.enable()
            gc.collect()

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

    def test_queued_review_is_requeued_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            store.initialize()
            item = review()
            store.queue_if_needed(item)
            store.mark_queued(item.id)
            store.initialize()
            self.assertEqual(store.due_reviews()[0].id, item.id)

    def test_existing_exports_create_reconciliation_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "existing.mp4").touch()
            before = time.time()
            store = ReviewStore(output)
            store.initialize()
            self.assertGreaterEqual(store.reconciliation_cutoff, before)

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
