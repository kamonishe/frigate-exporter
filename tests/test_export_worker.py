import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.models.config import Config
from app.models.review import Review
from app.queue.review_queue import ReviewQueue
from app.services.review_store import ReviewStore
from app.services.runtime_status import RuntimeStatus
from app.workers.export_worker import ExportTimeoutError, ExportWorker


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
                "export_timeout": 1,
                "max_attempts": 10,
            },
            "retention": {},
            "filters": {
                "cameras": [],
                "labels": [],
                "severity": [],
            },
            "logging": {"level": "INFO"},
        }
    )


class ExportWorkerTests(unittest.TestCase):
    def test_copy_reports_complete_byte_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source.mp4"
            destination = directory / "destination.mp4"
            contents = b"recording-data" * 100_000
            source.write_bytes(contents)

            config = make_config(directory)
            status = RuntimeStatus(config.mqtt.topic, 1)
            worker = ExportWorker(
                worker_id=1,
                queue=ReviewQueue(),
                frigate=None,  # type: ignore[arg-type]
                config=config,
                status=status,
            )

            worker._copy_with_progress(source, destination, len(contents))

            self.assertEqual(destination.read_bytes(), contents)
            snapshot = status.snapshot(0)["workers"][0]
            self.assertEqual(snapshot["phase"], "copying")
            self.assertEqual(snapshot["bytes_copied"], len(contents))
            self.assertEqual(snapshot["total_bytes"], len(contents))
            self.assertEqual(snapshot["progress_percent"], 100.0)

    def test_event_timestamp_uses_review_end_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "export.mp4"
            destination.write_bytes(b"recording")
            event_time = 1_700_000_000.0

            ExportWorker._set_event_timestamp(destination, event_time)

            self.assertAlmostEqual(destination.stat().st_mtime, event_time)

    def test_wait_for_export_returns_completed_export(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                config = make_config(directory)
                frigate = AsyncMock()
                frigate.list_exports.side_effect = [
                    [],
                    [{"id": "export-1", "in_progress": True}],
                    [{
                        "id": "export-1",
                        "in_progress": False,
                        "video_path": "/media/frigate/export.mp4",
                    }],
                ]
                worker = ExportWorker(
                    worker_id=1,
                    queue=ReviewQueue(),
                    frigate=frigate,
                    config=config,
                    status=RuntimeStatus(config.mqtt.topic, 1),
                )

                with patch(
                    "app.workers.export_worker.asyncio.sleep",
                    new=AsyncMock(),
                ):
                    result = await worker._wait_for_export("export-1")

                self.assertFalse(result["in_progress"])
                self.assertEqual(frigate.list_exports.await_count, 3)

        asyncio.run(run())

    def test_timed_out_export_fails_review_and_releases_worker(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                config = make_config(directory)
                store = ReviewStore(directory)
                store.initialize()
                item = Review(
                    id="review-1",
                    camera="camera1",
                    start_time=1,
                    end_time=2,
                    severity="alert",
                    thumb_path="",
                )
                store.queue_if_needed(item)
                store.mark_queued(item.id)

                frigate = AsyncMock()
                frigate.start_export.return_value = {"export_id": "export-1"}
                frigate.list_exports.return_value = []
                queue = ReviewQueue()
                await queue.put(item)
                worker = ExportWorker(
                    worker_id=1,
                    queue=queue,
                    frigate=frigate,
                    config=config,
                    status=RuntimeStatus(config.mqtt.topic, 1),
                    store=store,
                )
                task = asyncio.create_task(worker.run())
                try:
                    await asyncio.wait_for(queue.join(), timeout=3)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

                self.assertEqual(store.summary(), {"failed": 1})
                frigate.delete_exports.assert_awaited_once_with(["export-1"])

        asyncio.run(run())

    def test_timed_out_export_becomes_unavailable_at_attempt_limit(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                config_data = make_config(directory).model_dump()
                config_data["export"]["max_attempts"] = 1
                config = Config.model_validate(config_data)
                store = ReviewStore(directory)
                store.initialize()
                item = Review(
                    id="review-1",
                    camera="camera1",
                    start_time=1,
                    end_time=2,
                    severity="alert",
                    thumb_path="",
                )
                store.queue_if_needed(item)
                store.mark_queued(item.id)

                frigate = AsyncMock()
                frigate.start_export.return_value = {"export_id": "export-1"}
                frigate.list_exports.return_value = []
                queue = ReviewQueue()
                await queue.put(item)
                worker = ExportWorker(
                    worker_id=1,
                    queue=queue,
                    frigate=frigate,
                    config=config,
                    status=RuntimeStatus(config.mqtt.topic, 1),
                    store=store,
                )
                task = asyncio.create_task(worker.run())
                try:
                    await asyncio.wait_for(queue.join(), timeout=3)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

                self.assertEqual(store.summary(), {"unavailable": 1})

        asyncio.run(run())

    def test_wait_for_missing_export_times_out(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                config = make_config(directory)
                frigate = AsyncMock()
                frigate.list_exports.return_value = []
                worker = ExportWorker(
                    worker_id=1,
                    queue=ReviewQueue(),
                    frigate=frigate,
                    config=config,
                    status=RuntimeStatus(config.mqtt.topic, 1),
                )

                clock = iter((0.0, 0.5, 1.1))
                with (
                    patch(
                        "app.workers.export_worker.time.monotonic",
                        side_effect=clock,
                    ),
                    patch(
                        "app.workers.export_worker.asyncio.sleep",
                        new=AsyncMock(),
                    ),
                    self.assertRaisesRegex(
                        ExportTimeoutError,
                        "export-1",
                    ),
                ):
                    await worker._wait_for_export("export-1")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
