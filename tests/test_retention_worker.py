import os
import tempfile
import time
import unittest
from pathlib import Path

from app.models.config import Config
from app.models.review import Review
from app.services.review_store import ReviewStore
from app.services.runtime_status import RuntimeStatus
from app.workers.retention_worker import RetentionWorker


def make_config(output: Path) -> Config:
    return Config.model_validate(
        {
            "frigate": {
                "url": "https://frigate:8971",
                "username": "test",
                "password": "test",
            },
            "mqtt": {
                "host": "mqtt",
                "port": 1883,
                "username": "test",
                "password": "test",
                "topic": "frigate/reviews",
            },
            "export": {
                "output": str(output),
                "workers": 1,
                "pre_capture": 1,
                "post_capture": 1,
            },
            "retention": {
                "enabled": True,
                "days": 1,
                "check_interval_hours": 1,
            },
            "filters": {
                "cameras": [],
                "labels": [],
                "severity": [],
            },
            "logging": {"level": "INFO"},
        }
    )


class RetentionWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_supports_one_hour_retention(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            expired = directory / "expired.mp4"
            retained = directory / "retained.mp4"
            expired.write_bytes(b"a")
            retained.write_bytes(b"b")
            old = time.time() - 2 * 3600
            os.utime(expired, (old, old))

            config_data = make_config(directory).model_dump()
            config_data["retention"] = {
                "enabled": True,
                "hours": 1,
                "check_interval_hours": 1,
            }
            config = Config.model_validate(config_data)
            status = RuntimeStatus(config.mqtt.topic, 1)
            worker = RetentionWorker(config, status)

            await worker.cleanup()

            snapshot = status.snapshot(0)
            self.assertFalse(expired.exists())
            self.assertTrue(retained.exists())
            self.assertEqual(snapshot["retention"]["hours"], 1)
            self.assertIsNone(snapshot["retention"]["days"])
            self.assertEqual(snapshot["retention"]["retention_seconds"], 3600)

    async def test_cleanup_updates_storage_and_retention_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            expired = directory / "expired.mp4"
            retained = directory / "retained.mp4"
            expired.write_bytes(b"a" * 1024)
            retained.write_bytes(b"b" * 2048)
            old = time.time() - 2 * 24 * 3600
            os.utime(expired, (old, old))

            config = make_config(directory)
            status = RuntimeStatus(config.mqtt.topic, 1)
            store = ReviewStore(directory)
            store.initialize()
            unavailable = Review(
                id="missing",
                camera="cam",
                start_time=1,
                end_time=2,
                severity="alert",
                thumb_path="",
            )
            store.queue_if_needed(unavailable)
            store.mark_queued(unavailable.id)
            store.mark_unavailable(unavailable.id, "No recordings found for time range")
            worker = RetentionWorker(config, status, store)

            with self.assertLogs(
                "frigate_exporter.retention",
                level="INFO",
            ) as logs:
                await worker.cleanup()

            snapshot = status.snapshot(0)

            self.assertFalse(expired.exists())
            self.assertTrue(retained.exists())
            self.assertEqual(snapshot["storage"]["file_count"], 1)
            self.assertEqual(snapshot["storage"]["total_bytes"], 2048)
            self.assertEqual(snapshot["retention"]["scanned"], 2)
            self.assertEqual(snapshot["retention"]["deleted"], 1)
            self.assertEqual(snapshot["retention"]["unavailable_deleted"], 1)
            self.assertEqual(
                snapshot["retention"]["reclaimed_bytes"],
                1024,
            )
            self.assertTrue(snapshot["retention"]["enabled"])
            self.assertEqual(snapshot["retention"]["days"], 1)
            self.assertIsNotNone(snapshot["retention"]["cutoff_at"])
            self.assertIsNotNone(snapshot["retention"]["last_run_at"])
            self.assertIsNotNone(snapshot["retention"]["next_run_at"])
            self.assertIn(
                "1 exported recordings use 2.0 KB",
                logs.output[0],
            )


if __name__ == "__main__":
    unittest.main()
