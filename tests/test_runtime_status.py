import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web

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
        self.assertIsNone(config.dashboard.username)
        self.assertIsNone(config.dashboard.password)

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
        status.set_worker_phase(1, "exporting")
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
            DashboardConfig(username="viewer", password="secret"),
            status,
            queue,
        )
        authorization = base64.b64encode(b"viewer:secret").decode()
        request = SimpleNamespace(
            headers={"Authorization": f"Basic {authorization}"}
        )
        response = await dashboard._status_response(request)
        payload = json.loads(response.text)

        self.assertTrue(payload["connections"]["frigate"]["connected"])
        self.assertTrue(payload["connections"]["mqtt"]["connected"])
        self.assertEqual(payload["workers"][0]["state"], "processing")
        self.assertEqual(payload["workers"][0]["phase"], "exporting")
        self.assertEqual(payload["workers"][0]["export_id"], "export-1")
        self.assertEqual(payload["storage"]["total_size"], "1.5 KB")
        self.assertEqual(payload["retention"]["reclaimed_size"], "1.0 KB")
        self.assertGreaterEqual(len(payload["storage_history"]), 1)
        self.assertGreaterEqual(len(payload["retention_history"]), 1)

        status.set_worker_idle(1)
        idle = status.snapshot(0)["workers"][0]

        self.assertEqual(idle["state"], "idle")
        self.assertIsNotNone(idle["last_completed_at"])
        self.assertEqual(idle["last_result"], "success")

    async def test_dashboard_restart_is_authenticated_and_rate_limited(self) -> None:
        status = RuntimeStatus("frigate/reviews", 1)
        restarted = asyncio.Event()
        dashboard = DashboardServer(
            DashboardConfig(username="viewer", password="secret"),
            status,
            ReviewQueue(),
            restart_callback=restarted.set,
        )
        authorization = base64.b64encode(b"viewer:secret").decode()
        request = SimpleNamespace(
            headers={
                "Authorization": f"Basic {authorization}",
                "X-Exporter-Request": "dashboard",
            },
            cookies={},
        )

        response = await dashboard._restart_response(request)
        self.assertEqual(response.status, 202)
        await asyncio.wait_for(restarted.wait(), timeout=1)

        with self.assertRaises(web.HTTPTooManyRequests):
            await dashboard._restart_response(request)

    async def test_dashboard_manual_runs_are_authenticated(self) -> None:
        status = RuntimeStatus("frigate/reviews", 1)
        retention_called = asyncio.Event()
        reconciliation_called = asyncio.Event()
        dashboard = DashboardServer(
            DashboardConfig(username="viewer", password="secret"),
            status,
            ReviewQueue(),
            retention_callback=lambda: retention_called.set() or True,
            reconciliation_callback=lambda: reconciliation_called.set() or True,
        )
        authorization = base64.b64encode(b"viewer:secret").decode()
        request = SimpleNamespace(
            headers={
                "Authorization": f"Basic {authorization}",
                "X-Exporter-Request": "dashboard",
            },
            cookies={},
        )

        retention_response = await dashboard._retention_response(request)
        reconciliation_response = await dashboard._reconciliation_response(request)
        self.assertEqual(retention_response.status, 202)
        self.assertEqual(reconciliation_response.status, 202)
        self.assertTrue(retention_called.is_set())
        self.assertTrue(reconciliation_called.is_set())

    async def test_dashboard_requires_auth_but_health_is_public(self) -> None:
        status = RuntimeStatus("frigate/reviews", 1)
        dashboard = DashboardServer(
            DashboardConfig(username="viewer", password="secret"),
            status,
            ReviewQueue(),
        )
        request = SimpleNamespace(headers={})

        with self.assertRaises(web.HTTPUnauthorized):
            await dashboard._status_response(request)

        health = await dashboard._health(request)
        self.assertEqual(json.loads(health.text), {"status": "ok"})

    def test_connection_states_progress_and_bounded_redacted_logs(self) -> None:
        status = RuntimeStatus(
            "frigate/reviews",
            1,
            secrets=["super-secret"],
        )
        status.set_mqtt_state("reconnecting", "bad super-secret")
        status.set_mqtt_state("connected")
        status.set_frigate_state("disconnected", "offline")

        review = Review.model_validate(
            {
                "id": "review-2",
                "camera": "back",
                "start_time": 1,
                "end_time": 2,
                "severity": "alert",
                "thumb_path": "/tmp/thumb.webp",
            }
        )
        status.set_worker_review(1, review)
        status.set_worker_copy_progress(1, 25, 100)

        for number in range(12):
            status.add_log(
                "INFO",
                "test",
                f"entry {number} super-secret",
            )

        snapshot = status.snapshot(0)
        self.assertEqual(
            snapshot["connections"]["mqtt"]["state"],
            "connected",
        )
        self.assertEqual(
            snapshot["connections"]["mqtt"]["last_error"],
            "bad [REDACTED]",
        )
        self.assertEqual(snapshot["workers"][0]["phase"], "copying")
        self.assertEqual(snapshot["workers"][0]["progress_percent"], 25.0)
        self.assertEqual(len(snapshot["logs"]), 10)
        self.assertEqual(snapshot["logs"][0]["message"], "entry 2 [REDACTED]")


    def test_reconciliation_progress_is_visible_while_processing(self) -> None:
        status = RuntimeStatus("frigate/reviews", 1)
        status.start_reconciliation()
        status.update_reconciliation_progress(discovered=10, queued=3, pending=3)
        active = status.snapshot(3)["reconciliation"]
        self.assertEqual(active["state"], "processing")
        self.assertEqual(active["active"]["discovered"], 10)
        self.assertEqual(active["active"]["queued"], 3)
        self.assertEqual(active["active"]["pending"], 3)

        status.set_reconciliation_result(
            discovered=10, queued=3, processed=3, pending=0, unavailable=1
        )
        result = status.snapshot(0)["reconciliation"]
        self.assertEqual(result["state"], "success")
        self.assertEqual(result["processed"], 3)
        self.assertIsNone(result["active"])

    def test_camera_activity_tracks_success_and_failure_details(self) -> None:
        status = RuntimeStatus("frigate/reviews", 1, secrets=["secret"])
        review = Review.model_validate({
            "id": "camera-review",
            "camera": "front",
            "start_time": 1,
            "end_time": 2,
            "severity": "alert",
            "thumb_path": "/tmp/thumb.webp",
        })
        status.set_worker_review(1, review)
        status.set_worker_idle(1, bytes_exported=2048)
        status.set_worker_review(1, review)
        status.set_worker_idle(1, "failed secret", result_kind="failed")
        stats = status.snapshot(0)["camera_stats"]["front"]
        self.assertEqual(stats["exported"], 1)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["bytes_exported"], 2048)
        self.assertEqual(stats["recent_failures"][0]["error"], "failed [REDACTED]")

    def test_reconciliation_result_reports_drained_success(self) -> None:
        status = RuntimeStatus("frigate/reviews", 1)
        status.set_reconciliation_result(discovered=6, queued=2, pending=0, unavailable=3)
        result = status.snapshot(0)["reconciliation"]
        self.assertEqual(result["state"], "success")
        self.assertEqual(result["pending"], 0)
        self.assertEqual(result["unavailable"], 3)
        self.assertIsNone(result["last_error"])

        status.set_reconciliation_result(discovered=1, queued=1, pending=1)
        self.assertEqual(status.snapshot(1)["reconciliation"]["state"], "pending")


    def test_history_persists_and_uses_retention_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            history_path = Path(directory) / "history.json"
            first = RuntimeStatus("frigate/reviews", 1, history_path=history_path)
            first.configure_retention(enabled=True, days=7, check_interval_hours=1)
            first.set_storage(ExportStorageSummary(file_count=1, total_bytes=1024))
            restored = RuntimeStatus("frigate/reviews", 1, history_path=history_path)
            restored.configure_retention(enabled=True, days=7, check_interval_hours=1)
            self.assertEqual(len(restored.snapshot(0)["storage_history"]), 1)


if __name__ == "__main__":
    unittest.main()
