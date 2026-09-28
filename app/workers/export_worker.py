from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from app.clients.frigate import FrigateClient
from app.models.config import Config
from app.models.review import Review
from app.queue.review_queue import ReviewQueue
from app.services.runtime_status import RuntimeStatus
from app.services.storage import summarize_export_storage

logger = logging.getLogger("frigate_exporter.worker")


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
    ) -> None:
        self._worker_id = worker_id
        self._queue = queue
        self._frigate = frigate
        self._config = config
        self._status = status

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

            try:
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

                last_state = None

                while True:
                    exports = await self._frigate.list_exports()

                    match = next(
                        (
                            e
                            for e in exports
                            if e["id"] == export_id
                        ),
                        None,
                    )

                    if match is None:
                        if last_state != "waiting":
                            logger.debug(
                                "Worker #%d waiting...",
                                self._worker_id,
                            )
                            last_state = "waiting"

                        await asyncio.sleep(1)
                        continue

                    if match["in_progress"]:
                        if last_state != "running":
                            logger.debug(
                                "Worker #%d exporting...",
                                self._worker_id,
                            )
                            last_state = "running"

                        await asyncio.sleep(1)
                        continue

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

                    shutil.copy2(source, destination)

                    source_size = source.stat().st_size
                    destination_size = destination.stat().st_size

                    if source_size != destination_size:
                        raise RuntimeError(
                            "Copied file size does not match source."
                        )

                    self._status.set_storage(
                        summarize_export_storage(self._output_dir)
                    )

                    logger.debug(
                        "Requesting Frigate cleanup"
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

                    break

            except Exception as exc:
                logger.exception(
                    "Worker #%d failed processing review %s",
                    self._worker_id,
                    review.id,
                )
                error = str(exc)

            finally:
                self._status.set_worker_idle(
                    self._worker_id,
                    error=error,
                )
                self._queue.task_done()
