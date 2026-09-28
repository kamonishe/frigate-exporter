from __future__ import annotations

from datetime import UTC, datetime

from app.core.version import APP_VERSION
from app.models.review import Review
from app.services.storage import ExportStorageSummary, format_bytes


class RuntimeStatus:
    """In-memory status exposed by the read-only dashboard."""

    def __init__(self, mqtt_topic: str, worker_count: int) -> None:
        self._started_at = self._now()
        self._mqtt_topic = mqtt_topic
        self._mqtt_connected = False
        self._frigate_connected = False
        self._frigate_version: str | None = None
        self._storage: ExportStorageSummary | None = None
        self._retention: dict[str, object] | None = None
        self._workers = {
            worker_id: self._new_worker()
            for worker_id in range(1, worker_count + 1)
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _new_worker() -> dict[str, object]:
        return {
            "state": "idle",
            "review_id": None,
            "camera": None,
            "export_id": None,
            "started_at": None,
            "last_completed_at": None,
            "last_error": None,
        }

    def set_mqtt_connected(self, connected: bool) -> None:
        self._mqtt_connected = connected

    def set_frigate_connected(self, version: str) -> None:
        self._frigate_connected = True
        self._frigate_version = version

    def set_worker_review(self, worker_id: int, review: Review) -> None:
        worker = self._workers[worker_id]
        worker.update(
            {
                "state": "processing",
                "review_id": review.id,
                "camera": review.camera,
                "export_id": None,
                "started_at": self._now(),
                "last_error": None,
            }
        )

    def set_worker_export(self, worker_id: int, export_id: str) -> None:
        self._workers[worker_id]["export_id"] = export_id

    def set_worker_idle(
        self,
        worker_id: int,
        error: str | None = None,
    ) -> None:
        worker = self._workers[worker_id]
        worker.update(
            {
                "state": "idle",
                "review_id": None,
                "camera": None,
                "export_id": None,
                "started_at": None,
                "last_completed_at": self._now(),
                "last_error": error,
            }
        )

    def set_storage(self, summary: ExportStorageSummary) -> None:
        self._storage = summary

    def set_retention_result(
        self,
        *,
        scanned: int,
        deleted: int,
        reclaimed_bytes: int,
        storage: ExportStorageSummary,
    ) -> None:
        self._storage = storage
        self._retention = {
            "last_run_at": self._now(),
            "scanned": scanned,
            "deleted": deleted,
            "reclaimed_bytes": reclaimed_bytes,
            "reclaimed_size": format_bytes(reclaimed_bytes),
        }

    def snapshot(self, queue_size: int) -> dict[str, object]:
        storage = self._storage

        return {
            "exporter": {
                "version": APP_VERSION,
                "started_at": self._started_at,
            },
            "connections": {
                "frigate": {
                    "connected": self._frigate_connected,
                    "version": self._frigate_version,
                },
                "mqtt": {
                    "connected": self._mqtt_connected,
                    "topic": self._mqtt_topic,
                },
            },
            "queue": {"pending": queue_size},
            "workers": [
                {"id": worker_id, **worker}
                for worker_id, worker in self._workers.items()
            ],
            "storage": (
                {
                    "file_count": storage.file_count,
                    "total_bytes": storage.total_bytes,
                    "total_size": format_bytes(storage.total_bytes),
                }
                if storage is not None
                else None
            ),
            "retention": self._retention,
        }
