from __future__ import annotations

import json
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from app.core.version import APP_VERSION
from app.models.review import Review
from app.services.storage import ExportStorageSummary, format_bytes


class RuntimeStatus:
    """Thread-safe-enough runtime status with persisted dashboard history."""

    def __init__(
        self,
        mqtt_topic: str,
        worker_count: int,
        *,
        secrets: list[str] | None = None,
        history_path: Path | None = None,
    ) -> None:
        self._started_at = self._now()
        self._history_path = history_path
        self._history_days = 1
        self._secrets = [value for value in (secrets or []) if value]
        self._connections = {
            "frigate": self._new_connection(version=None),
            "mqtt": self._new_connection(topic=mqtt_topic),
        }
        self._storage: ExportStorageSummary | None = None
        self._reconciliation: dict[str, object] = {
            "enabled": False,
            "state": "idle",
            "last_run_at": None,
            "last_success_at": None,
            "last_error": None,
            "discovered": 0,
            "queued": 0,
            "pending": 0,
        }
        self._retention: dict[str, object] = {
            "enabled": False,
            "days": None,
            "check_interval_hours": None,
            "last_run_at": None,
            "cutoff_at": None,
            "next_run_at": None,
            "scanned": 0,
            "deleted": 0,
            "reclaimed_bytes": 0,
            "reclaimed_size": format_bytes(0),
        }
        self._workers = {
            worker_id: self._new_worker()
            for worker_id in range(1, worker_count + 1)
        }
        self._worker_started_monotonic: dict[int, float | None] = {
            worker_id: None for worker_id in self._workers
        }
        self._logs: deque[dict[str, str]] = deque(maxlen=10)
        self._storage_history: deque[dict[str, object]] = deque(maxlen=2000)
        self._retention_history: deque[dict[str, object]] = deque(maxlen=2000)
        self._reconciliation_history: deque[dict[str, object]] = deque(maxlen=2000)
        self._load_history()
    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _redact(self, value: str | None) -> str | None:
        if value is None:
            return None
        for secret in self._secrets:
            value = value.replace(secret, "[REDACTED]")
        return value

    def _load_history(self) -> None:
        if self._history_path is None or not self._history_path.exists():
            return
        try:
            data = json.loads(self._history_path.read_text())
            for key, target in (("storage", self._storage_history), ("retention", self._retention_history), ("reconciliation", self._reconciliation_history)):
                target.extend(data.get(key, []))
        except (OSError, TypeError, ValueError):
            return

    def _persist_history(self) -> None:
        if self._history_path is None:
            return
        data = {"storage": list(self._storage_history), "retention": list(self._retention_history), "reconciliation": list(self._reconciliation_history)}
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._history_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data))
        temporary.replace(self._history_path)

    def _new_connection(self, **details: object) -> dict[str, object]:
        return {
            "state": "disconnected",
            "connected": False,
            "last_error": None,
            "last_connected_at": None,
            "last_state_change_at": self._now(),
            **details,
        }

    @staticmethod
    def _new_worker() -> dict[str, object]:
        return {
            "state": "idle",
            "phase": "idle",
            "review_id": None,
            "camera": None,
            "export_id": None,
            "started_at": None,
            "progress_percent": None,
            "bytes_copied": None,
            "total_bytes": None,
            "last_completed_at": None,
            "last_elapsed_seconds": None,
            "last_result": None,
            "last_error": None,
        }

    def _set_connection(
        self,
        name: str,
        state: str,
        error: str | None = None,
    ) -> None:
        if state not in {"connected", "disconnected", "reconnecting"}:
            raise ValueError(f"Unsupported connection state: {state}")

        connection = self._connections[name]
        if connection["state"] != state:
            connection["last_state_change_at"] = self._now()
        connection["state"] = state
        connection["connected"] = state == "connected"
        if error is not None:
            connection["last_error"] = self._redact(error)
        if state == "connected":
            connection["last_connected_at"] = self._now()

    def set_mqtt_state(
        self,
        state: str,
        error: str | None = None,
    ) -> None:
        self._set_connection("mqtt", state, error)

    def set_mqtt_connected(self, connected: bool) -> None:
        self.set_mqtt_state("connected" if connected else "disconnected")

    def set_frigate_state(
        self,
        state: str,
        error: str | None = None,
    ) -> None:
        self._set_connection("frigate", state, error)

    def set_frigate_connected(self, version: str) -> None:
        self._connections["frigate"]["version"] = version
        self.set_frigate_state("connected")

    def _recent_history(self, history: deque[dict[str, object]]) -> list[dict[str, object]]:
        cutoff = time.time() - self._history_days * 24 * 60 * 60
        return [{key: value for key, value in entry.items() if key != "epoch"} for entry in history if float(entry["epoch"]) >= cutoff]

    def _record_storage(self, summary: ExportStorageSummary, now: str) -> None:
        self._storage_history.append({"epoch": time.time(), "timestamp": now, "total_bytes": summary.total_bytes, "file_count": summary.file_count, "total_size": format_bytes(summary.total_bytes)})
        self._persist_history()

    def configure_reconciliation(self, *, enabled: bool) -> None:
        self._reconciliation["enabled"] = enabled

    def set_reconciliation_result(
        self,
        *,
        discovered: int,
        queued: int,
        pending: int,
        error: str | None = None,
    ) -> None:
        now = self._now()
        self._reconciliation.update(
            {
                "last_run_at": now,
                "state": "error" if error else ("success" if pending == 0 else "pending"),
                "discovered": discovered,
                "queued": queued,
                "pending": pending,
                "last_error": self._redact(error),
            }
        )
        if error is None:
            self._reconciliation["last_success_at"] = now
        self._reconciliation_history.append({"epoch": time.time(), "timestamp": now, "state": self._reconciliation["state"], "discovered": discovered, "queued": queued, "pending": pending, "error": self._redact(error)})
        self._persist_history()
    def update_reconciliation_queue(self, pending: int) -> None:
        """Refresh reconciliation state when export workers drain the durable queue."""
        if not self._reconciliation.get("last_run_at"):
            return
        self._reconciliation["pending"] = pending
        if pending == 0 and self._reconciliation.get("state") == "pending":
            now = self._now()
            self._reconciliation["state"] = "success"
            self._reconciliation["last_success_at"] = now
            self._reconciliation["last_error"] = None


    def configure_retention(
        self,
        *,
        enabled: bool,
        days: int,
        check_interval_hours: int,
    ) -> None:
        self._history_days = days if enabled else 1
        self._retention.update(
            {
                "enabled": enabled,
                "days": days,
                "check_interval_hours": check_interval_hours,
            }
        )

    def set_worker_review(self, worker_id: int, review: Review) -> None:
        worker = self._workers[worker_id]
        worker.update(
            {
                "state": "processing",
                "phase": "queued",
                "review_id": review.id,
                "camera": review.camera,
                "export_id": None,
                "started_at": self._now(),
                "progress_percent": None,
                "bytes_copied": None,
                "total_bytes": None,
                "last_result": None,
                "last_error": None,
            }
        )
        self._worker_started_monotonic[worker_id] = time.monotonic()

    def set_worker_export(self, worker_id: int, export_id: str) -> None:
        self._workers[worker_id]["export_id"] = export_id

    def set_worker_phase(self, worker_id: int, phase: str) -> None:
        worker = self._workers[worker_id]
        worker["phase"] = phase
        if phase != "copying":
            worker["progress_percent"] = None
            worker["bytes_copied"] = None
            worker["total_bytes"] = None

    def set_worker_copy_progress(
        self,
        worker_id: int,
        copied: int,
        total: int,
    ) -> None:
        worker = self._workers[worker_id]
        worker.update(
            {
                "phase": "copying",
                "bytes_copied": copied,
                "total_bytes": total,
                "progress_percent": round(copied * 100 / total, 1)
                if total
                else 100.0,
            }
        )

    def set_worker_idle(
        self,
        worker_id: int,
        error: str | None = None,
    ) -> None:
        worker = self._workers[worker_id]
        started = self._worker_started_monotonic[worker_id]
        elapsed = time.monotonic() - started if started is not None else None
        worker.update(
            {
                "state": "idle",
                "phase": "idle",
                "export_id": None,
                "progress_percent": None,
                "bytes_copied": None,
                "total_bytes": None,
                "last_completed_at": self._now(),
                "last_elapsed_seconds": round(elapsed, 1)
                if elapsed is not None
                else None,
                "last_result": "error" if error else "success",
                "last_error": self._redact(error),
            }
        )
        self._worker_started_monotonic[worker_id] = None

    def set_storage(self, summary: ExportStorageSummary) -> None:
        self._storage = summary
        self._record_storage(summary, self._now())

    def set_retention_next_run(self, next_run_at: str) -> None:
        self._retention["next_run_at"] = next_run_at

    def set_retention_result(
        self,
        *,
        scanned: int,
        deleted: int,
        reclaimed_bytes: int,
        storage: ExportStorageSummary,
        cutoff_at: str | None = None,
        next_run_at: str | None = None,
    ) -> None:
        self._storage = storage
        now = self._now()
        self._record_storage(storage, now)
        self._retention_history.append({"epoch": time.time(), "timestamp": now, "deleted": deleted, "reclaimed_bytes": reclaimed_bytes, "reclaimed_size": format_bytes(reclaimed_bytes), "scanned": scanned})
        self._persist_history()
        self._retention.update(
            {
                "last_run_at": self._now(),
                "cutoff_at": cutoff_at,
                "next_run_at": next_run_at,
                "scanned": scanned,
                "deleted": deleted,
                "reclaimed_bytes": reclaimed_bytes,
                "reclaimed_size": format_bytes(reclaimed_bytes),
            }
        )

    def add_log(self, level: str, logger: str, message: str) -> None:
        self._logs.append(
            {
                "timestamp": self._now(),
                "level": level,
                "logger": logger,
                "message": self._redact(message) or "",
            }
        )

    def snapshot(self, queue_size: int) -> dict[str, object]:
        storage = self._storage
        workers = []
        for worker_id, worker in self._workers.items():
            item = {"id": worker_id, **worker}
            started = self._worker_started_monotonic[worker_id]
            item["elapsed_seconds"] = (
                round(time.monotonic() - started, 1)
                if started is not None
                else None
            )
            workers.append(item)

        return {
            "exporter": {
                "version": APP_VERSION,
                "started_at": self._started_at,
            },
            "connections": {
                name: dict(connection)
                for name, connection in self._connections.items()
            },
            "queue": {"pending": queue_size},
            "workers": workers,
            "storage_history": self._recent_history(self._storage_history),
            "retention_history": self._recent_history(self._retention_history),
            "reconciliation_history": self._recent_history(self._reconciliation_history),
            "storage": (
                {
                    "file_count": storage.file_count,
                    "total_bytes": storage.total_bytes,
                    "total_size": format_bytes(storage.total_bytes),
                }
                if storage is not None
                else None
            ),
            "retention": dict(self._retention),
            "reconciliation": dict(self._reconciliation),
            "logs": list(self._logs),
        }
