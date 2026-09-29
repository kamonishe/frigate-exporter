import tempfile
import unittest
from pathlib import Path

from app.models.config import Config
from app.queue.review_queue import ReviewQueue
from app.services.runtime_status import RuntimeStatus
from app.workers.export_worker import ExportWorker


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


if __name__ == "__main__":
    unittest.main()
