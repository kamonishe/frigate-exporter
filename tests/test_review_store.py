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

    def test_queue_status_reports_oldest_active_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            store.initialize()
            item = review()
            self.assertTrue(store.queue_if_needed(item))
            status = store.queue_status()
            self.assertEqual(status["pending"], 1)
            oldest = status["oldest"]
            self.assertIsNotNone(oldest)
            self.assertEqual(oldest["review_id"], item.id)
            self.assertEqual(oldest["camera"], item.camera)
            self.assertEqual(oldest["state"], "pending")
            self.assertGreaterEqual(oldest["age_seconds"], 0)

    def test_failed_review_becomes_due_after_retry_delay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            store.initialize()
            item = review()
            store.queue_if_needed(item)
            store.mark_queued(item.id)
            store.fail(item.id, "temporary", retry_after=0)
            self.assertEqual(store.due_reviews()[0].id, item.id)

    def test_failed_review_becomes_unavailable_at_attempt_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            store.initialize()
            item = review()
            store.queue_if_needed(item)
            store.mark_queued(item.id)

            store.claim(item.id)
            self.assertFalse(
                store.fail(item.id, "temporary", retry_after=0, max_attempts=2)
            )
            store.mark_queued(item.id)
            store.claim(item.id)
            self.assertTrue(
                store.fail(item.id, "temporary", retry_after=0, max_attempts=2)
            )

            self.assertEqual(store.summary(), {"unavailable": 1})
            self.assertEqual(store.due_reviews(), [])

    def test_failed_review_outside_retention_becomes_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            store.initialize()
            item = review()
            store.queue_if_needed(item)
            store.mark_queued(item.id)
            store.claim(item.id)

            self.assertTrue(
                store.fail(
                    item.id,
                    "temporary",
                    max_attempts=10,
                    expire_before=3,
                )
            )
            self.assertEqual(store.summary(), {"unavailable": 1})

    def test_unavailable_review_is_terminal_and_expires_with_retention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            store.initialize()
            item = review()
            store.queue_if_needed(item)
            store.mark_queued(item.id)
            store.mark_unavailable(item.id, "No recordings found for time range")

            self.assertEqual(store.summary(), {"unavailable": 1})
            self.assertEqual(store.retryable_count(), 0)
            self.assertEqual(store.unavailable_count(), 1)
            self.assertFalse(store.queue_if_needed(item))
            self.assertEqual(store.due_reviews(), [])
            self.assertEqual(store.purge_unavailable(before=1), 0)
            self.assertEqual(store.purge_unavailable(before=3), 1)
            self.assertEqual(store.summary(), {})


if __name__ == "__main__":
    unittest.main()
