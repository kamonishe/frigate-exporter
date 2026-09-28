import json
import unittest

from app.models.config import Config, DashboardConfig
from app.models.review import Review
from app.queue.review_queue import ReviewQueue
from app.services.dashboard import DashboardServer
from app.services.runtime_status import RuntimeStatus
from app.services.storage import ExportStorageSummary


class RuntimeStatusTests(unittest.IsolatedAsyncioTestCase):
    def test_dashboard_config_defaults(self) -> None:
        config = Config.model_validate(
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
                    "output": "/exports",
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

        self.assertTrue(config.dashboard.enabled)
        self.assertEqual(config.dashboard.host, "0.0.0.0")
        self.assertEqual(config.dashboard.port, 5050)

    async def test_status_and_dashboard_response(self) -> None:
        status = RuntimeStatus("frigate/reviews", 1)
        review = Review.model_validate(
            {
                "id": "review-1",
                "camera": "front",
                "start_time": 1,
                "end_time": 2,
                "severity": "alert",
                "thumb_path": "/tmp/thumb.webp",
            }
        )

        status.set_frigate_connected("0.18.0")
        status.set_mqtt_connected(True)
        status.set_worker_review(1, review)
        status.set_worker_export(1, "export-1")
        status.set_storage(
            ExportStorageSummary(file_count=2, total_bytes=1536)
        )
        status.set_retention_result(
            scanned=3,
            deleted=1,
            reclaimed_bytes=1024,
            storage=ExportStorageSummary(
                file_count=2,
                total_bytes=1536,
            ),
        )

        queue = ReviewQueue()
        dashboard = DashboardServer(
            DashboardConfig(),
            status,
            queue,
        )
        response = await dashboard._status_response(None)
        payload = json.loads(response.text)

        self.assertTrue(payload["connections"]["frigate"]["connected"])
        self.assertTrue(payload["connections"]["mqtt"]["connected"])
        self.assertEqual(payload["workers"][0]["state"], "processing")
        self.assertEqual(payload["workers"][0]["export_id"], "export-1")
        self.assertEqual(payload["storage"]["total_size"], "1.5 KB")
        self.assertEqual(payload["retention"]["reclaimed_size"], "1.0 KB")

        status.set_worker_idle(1)
        idle = status.snapshot(0)["workers"][0]

        self.assertEqual(idle["state"], "idle")
        self.assertIsNotNone(idle["last_completed_at"])


if __name__ == "__main__":
    unittest.main()
