from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path

from app.clients.frigate import FrigateClient, RecordingUnavailableError
from app.models.config import Config
from app.models.review import Review
from app.queue.review_queue import ReviewQueue
from app.services.review_store import ReviewStore
from app.services.runtime_status import RuntimeStatus
from app.services.storage import summarize_export_storage

logger = logging.getLogger("frigate_exporter.worker")


class ExportTimeoutError(TimeoutError):
    """Raised when Frigate does not finish an export within the deadline."""


class ExportWorker:
    """
    Worker responsible for exporting completed reviews.
    """

    def __init__(
        self,
        worker_id: int,
        queue: ReviewQueue,
        frigate: FrigateClient,
        config: Config,
        status: RuntimeStatus,
        store: ReviewStore | None = None,
    ) -> None:
        self._worker_id = worker_id
        self._queue = queue
        self._frigate = frigate
        self._config = config
        self._status = status
        self._store = store

        self._output_dir = Path(
            self._config.export.output
        )

    async def run(self) -> None:
        logger.debug(
            "Worker #%d started",
            self._worker_id,
        )

        while True:
            review: Review = await self._queue.get()
            self._status.set_worker_review(self._worker_id, review)

            started = time.monotonic()
            error: str | None = None
            result_kind: str | None = None
            bytes_exported: int | None = None

            try:
                if self._store is not None:
                    self._store.claim(review.id)

                logger.debug("----------------------------------------")
                logger.debug(
                    "Worker #%d processing review %s",
                    self._worker_id,
                    review.id,
                )

                start_time = max(
                    0,
                    review.start_time
                    - self._config.export.pre_capture,
                )

                end_time = (
                    review.end_time
                    + self._config.export.post_capture
                )

                logger.debug(
                    "Export window: %.3f -> %.3f",
                    start_time,
                    end_time,
                )

                self._status.set_worker_phase(
                    self._worker_id,
                    "exporting",
                )

                export = await self._frigate.start_export(
                    camera=review.camera,
                    start_time=start_time,
                    end_time=end_time,
                )

                export_id = export["export_id"]
                self._status.set_worker_export(self._worker_id, export_id)

                logger.debug(
                    "Worker #%d queued export %s",
                    self._worker_id,
                    export_id,
                )

                try:
                    match = await self._wait_for_export(export_id)
                except ExportTimeoutError:
                    await self._cleanup_export(export_id)
                    raise

                logger.debug(
                    "Worker #%d export finished",
                    self._worker_id,
                )

                #
                # Frigate already returns the correct path.
                # We mount the Frigate media directory to
                # /media/frigate inside the container.
                #
                source = Path(
                    match["video_path"]
                )

                destination = (
                    self._output_dir
                    / source.name
                )

                self._output_dir.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                logger.debug(
                    "Copying %s -> %s",
                    source,
                    destination,
                )

                source_size = source.stat().st_size
                self._status.set_worker_copy_progress(
                    self._worker_id,
                    0,
                    source_size,
                )
                await asyncio.to_thread(
                    self._copy_with_progress,
                    source,
                    destination,
                    source_size,
                )
                destination_size = destination.stat().st_size
                bytes_exported = destination_size

                if source_size != destination_size:
                    raise RuntimeError(
                        "Copied file size does not match source."
                    )

                # Retention follows the Frigate event time, not the time a
                # backlog export happened to finish copying locally.
                self._set_event_timestamp(destination, review.end_time)

                self._status.set_storage(
                    summarize_export_storage(self._output_dir)
                )

                logger.debug(
                    "Requesting Frigate cleanup"
                )

                self._status.set_worker_phase(
                    self._worker_id,
                    "cleanup",
                )

                await self._frigate.delete_exports(
                    [export_id]
                )

                elapsed = (
                    time.monotonic()
                    - started
                )

                logger.info(
                    "Export | %s | %.2f MB | %.2f s",
                    destination.name,
                    destination_size / 1024 / 1024,
                    elapsed,
                )

                logger.debug("----------------------------------------")

                if self._store is not None:
                    self._store.complete(review.id)
                result_kind = "success"

            except RecordingUnavailableError as exc:
                logger.warning(
                    "Worker #%d cannot export review %s: recording unavailable; will not retry",
                    self._worker_id,
                    review.id,
                )
                error = str(exc)
                result_kind = "unavailable"
                if self._store is not None:
                    self._store.mark_unavailable(review.id, error)
            except Exception as exc:
                error = str(exc)
                terminal = False
                if self._store is not None:
                    retention_cutoff = (
                        time.time()
                        - self._config.retention.retention_seconds
                        if self._config.retention.enabled
                        else None
                    )
                    terminal = self._store.fail(
                        review.id,
                        error,
                        max_attempts=self._config.export.max_attempts,
                        expire_before=retention_cutoff,
                    )
                result_kind = "unavailable" if terminal else "failed"
                if terminal:
                    logger.warning(
                        "Worker #%d stopped retrying review %s: %s",
                        self._worker_id,
                        review.id,
                        error,
                    )
                else:
                    logger.exception(
                        "Worker #%d failed processing review %s",
                        self._worker_id,
                        review.id,
                    )

            finally:
                self._status.set_worker_idle(
                    self._worker_id,
                    error=error,
                    result_kind=result_kind,
                    bytes_exported=bytes_exported,
                )
                if self._store is not None:
                    self._status.update_reconciliation_queue(
                        self._store.retryable_count(),
                        self._store.unavailable_count(),
                    )
                self._queue.task_done()

    async def _wait_for_export(self, export_id: str) -> dict:
        started = time.monotonic()
        deadline = started + self._config.export.export_timeout
        missing_deadline = started + min(60, self._config.export.export_timeout)
        last_state: str | None = None

        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                raise ExportTimeoutError(
                    f"Frigate export {export_id} did not finish within "
                    f"{self._config.export.export_timeout} seconds"
                )

            exports = await self._frigate.list_exports()
            match = next(
                (item for item in exports if item.get("id") == export_id),
                None,
            )

            if match is None:
                if last_state != "waiting":
                    logger.debug(
                        "Worker #%d waiting for Frigate export %s",
                        self._worker_id,
                        export_id,
                    )
                    last_state = "waiting"
                if time.monotonic() >= missing_deadline:
                    raise ExportTimeoutError(
                        f"Frigate export {export_id} disappeared or was not "
                        "reported by Frigate within 60 seconds"
                    )
            elif match.get("in_progress"):
                if last_state != "running":
                    logger.debug(
                        "Worker #%d waiting for Frigate export %s to finish",
                        self._worker_id,
                        export_id,
                    )
                    last_state = "running"
            else:
                return match

            await asyncio.sleep(min(1, remaining))

    async def _cleanup_export(self, export_id: str) -> None:
        try:
            await self._frigate.delete_exports([export_id])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Unable to clean up timed-out Frigate export %s: %s",
                export_id,
                exc,
            )

    @staticmethod
    def _set_event_timestamp(destination: Path, event_time: float) -> None:
        access_time = destination.stat().st_atime
        os.utime(destination, (access_time, event_time))

    def _copy_with_progress(
        self,
        source: Path,
        destination: Path,
        total_bytes: int,
    ) -> None:
        copied = 0
        with source.open("rb") as source_file, destination.open("wb") as output:
            while chunk := source_file.read(1024 * 1024):
                output.write(chunk)
                copied += len(chunk)
                self._status.set_worker_copy_progress(
                    self._worker_id,
                    copied,
                    total_bytes,
                )

        shutil.copystat(source, destination)
