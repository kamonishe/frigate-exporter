from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.models.config import Config
from app.services.review_store import ReviewStore
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
        store: ReviewStore | None = None,
    ) -> None:
        self._config = config
        self._status = status
        self._store = store
        self._directory = Path(config.export.output)
        self._trigger = asyncio.Event()
        self._status.configure_retention(
            enabled=config.retention.enabled,
            days=config.retention.days,
            check_interval_hours=config.retention.check_interval_hours,
        )

    def trigger_cleanup(self) -> bool:
        """Request an immediate cleanup without overlapping the scheduler."""
        if not self._config.retention.enabled:
            return False
        logger.info("Scheduling manual retention cleanup")
        self._trigger.set()
        return True

    async def run(self) -> None:
        if not self._config.retention.enabled:
            logger.debug("Retention worker disabled.")
            return

        logger.debug(
            "Retention worker started (keeping %d days, checking every %d hour(s)).",
            self._config.retention.days,
            self._config.retention.check_interval_hours,
        )

        first_run = True
        while True:
            if not first_run:
                try:
                    await asyncio.wait_for(
                        self._trigger.wait(),
                        timeout=self._config.retention.check_interval_hours * 3600,
                    )
                except TimeoutError:
                    pass
                self._trigger.clear()
            first_run = False
            try:
                await self.cleanup()
            except Exception:
                logger.exception("Retention cleanup failed.")

            next_run_at = datetime.now(UTC) + timedelta(
                hours=self._config.retention.check_interval_hours
            )
            self._status.set_retention_next_run(next_run_at.isoformat())

            logger.debug(
                "Next retention cleanup in %d hour(s).",
                self._config.retention.check_interval_hours,
            )


    async def cleanup(self) -> None:
        if not self._directory.exists():
            logger.warning(
                "Retention directory does not exist: %s",
                self._directory,
            )
        else:
            logger.debug("Starting retention cleanup...")

        cutoff = (
            time.time()
            - self._config.retention.days * 24 * 3600
        )
        next_run_at = datetime.now(UTC) + timedelta(
            hours=self._config.retention.check_interval_hours
        )

        scanned = 0
        deleted = 0
        reclaimed_bytes = 0
        unavailable_deleted = (
            self._store.purge_unavailable(cutoff)
            if self._store is not None
            else 0
        )

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
            cutoff_at=datetime.fromtimestamp(cutoff, UTC).isoformat(),
            next_run_at=next_run_at.isoformat(),
            unavailable_deleted=unavailable_deleted,
        )
        if self._store is not None:
            self._status.set_reconciliation_unavailable(
                self._store.unavailable_count()
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
        if unavailable_deleted:
            logger.info(
                "Retention removed %d unavailable review record(s) outside the retention window.",
                unavailable_deleted,
            )

        logger.debug(
            "Retention cleanup complete. Scanned %d file(s).",
            scanned,
        )
