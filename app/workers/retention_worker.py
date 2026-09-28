from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from app.models.config import Config
from app.services.runtime_status import RuntimeStatus
from app.services.storage import format_bytes, summarize_export_storage

logger = logging.getLogger("frigate_exporter.retention")


class RetentionWorker:
    """
    Periodically removes exported videos older than the configured retention period.
    """

    def __init__(
        self,
        config: Config,
        status: RuntimeStatus,
    ) -> None:
        self._config = config
        self._status = status
        self._directory = Path(config.export.output)

    async def run(self) -> None:
        if not self._config.retention.enabled:
            logger.debug("Retention worker disabled.")
            return

        logger.debug(
            "Retention worker started (keeping %d days, checking every %d hour(s)).",
            self._config.retention.days,
            self._config.retention.check_interval_hours,
        )

        while True:
            try:
                await self.cleanup()
            except Exception:
                logger.exception("Retention cleanup failed.")

            logger.debug(
                "Next retention cleanup in %d hour(s).",
                self._config.retention.check_interval_hours,
            )

            await asyncio.sleep(
                self._config.retention.check_interval_hours * 3600
            )

    async def cleanup(self) -> None:
        if not self._directory.exists():
            logger.warning(
                "Retention directory does not exist: %s",
                self._directory,
            )
            return

        logger.debug("Starting retention cleanup...")

        cutoff = (
            time.time()
            - self._config.retention.days * 24 * 3600
        )

        scanned = 0
        deleted = 0
        reclaimed_bytes = 0

        for file in self._directory.glob("*.mp4"):
            scanned += 1

            try:
                stat = file.stat()

                if stat.st_mtime >= cutoff:
                    continue

                reclaimed_bytes += stat.st_size

                file.unlink()

                deleted += 1

                logger.debug("Deleted %s", file.name)

            except Exception:
                logger.exception(
                    "Failed deleting %s",
                    file,
                )

        storage = summarize_export_storage(self._directory)
        self._status.set_retention_result(
            scanned=scanned,
            deleted=deleted,
            reclaimed_bytes=reclaimed_bytes,
            storage=storage,
        )

        if deleted == 0:
            logger.info(
                "Retention complete: no expired recordings removed; "
                "%d exported recordings use %s.",
                storage.file_count,
                format_bytes(storage.total_bytes),
            )
        else:
            logger.info(
                "Retention complete: removed %d expired recording(s) "
                "(%s reclaimed); %d exported recordings use %s.",
                deleted,
                format_bytes(reclaimed_bytes),
                storage.file_count,
                format_bytes(storage.total_bytes),
            )

        logger.debug(
            "Retention cleanup complete. Scanned %d file(s).",
            scanned,
        )
