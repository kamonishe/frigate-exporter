import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from test_export_worker import make_config
from test_review_store import review

from app.models.config import ReconciliationConfig
from app.queue.review_queue import ReviewQueue
from app.services.review_store import ReviewStore
from app.services.runtime_status import RuntimeStatus
from app.workers.export_worker import ExportWorker
from app.workers.reconciliation_worker import ReconciliationWorker


class BacklogTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_drains_1100_reviews_missing_from_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "exports"
            store = ReviewStore(output)
            store.initialize()
            for i in range(1100):
                item = review(str(i))
                store.queue_if_needed(item)
                store.mark_queued(item.id)
            store.initialize()
            source = root / "source.mp4"
            source.write_bytes(b"test recording")
            client = AsyncMock()
            client.list_reviews.return_value = []
            client.start_export.return_value = {"export_id": "export"}
            client.list_exports.return_value = [
                {"id": "export", "in_progress": False, "video_path": str(source)}
            ]
            queue = ReviewQueue()
            status = RuntimeStatus("test", 1)
            worker = ExportWorker(1, queue, client, make_config(output), status, store)
            task = asyncio.create_task(worker.run())
            try:
                reconciler = ReconciliationWorker(
                    ReconciliationConfig(), client, queue, store, status
                )
                await asyncio.wait_for(reconciler.reconcile(), timeout=180)
                self.assertEqual(store.summary(), {"completed": 1100})
                self.assertEqual(queue.size(), 0)
                self.assertEqual(client.start_export.await_count, 1100)
                self.assertEqual(client.delete_exports.await_count, 1100)
                self.assertEqual((output / source.name).read_bytes(), source.read_bytes())
                await reconciler.reconcile()
                self.assertEqual(client.start_export.await_count, 1100)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
