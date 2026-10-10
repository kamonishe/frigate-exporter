from __future__ import annotations

import asyncio
import logging
import time

from app.clients.frigate import FrigateClient
from app.models.config import ReconciliationConfig
from app.queue.review_queue import ReviewQueue
from app.services.review_store import ReviewStore
from app.services.runtime_status import RuntimeStatus

logger = logging.getLogger("frigate_exporter.reconciliation")

RECOVERY_RECONCILIATION_COOLDOWN = 30


class ReconciliationWorker:
    """Recover completed reviews missed by the MQTT event stream."""

    def __init__(
        self,
        config: ReconciliationConfig,
        frigate: FrigateClient,
        queue: ReviewQueue,
        store: ReviewStore,
        status: RuntimeStatus,
    ) -> None:
        self._config = config
        self._frigate = frigate
        self._queue = queue
        self._store = store
        self._status = status
        self._trigger = asyncio.Event()
        self._last_trigger_at = 0.0

    def trigger_reconcile(self, reason: str) -> None:
        """Request a recovery reconciliation, rate-limited to avoid storms."""
        now = time.monotonic()
        if now - self._last_trigger_at < RECOVERY_RECONCILIATION_COOLDOWN:
            return
        self._last_trigger_at = now
        logger.info("Scheduling recovery reconciliation after %s", reason)
        self._trigger.set()

    async def reconcile(self) -> None:
        now = time.time()
        reviews = await self._frigate.list_reviews(
            after=max(
                now - self._config.lookback_days * 86400,
                self._store.reconciliation_cutoff,
            ),
            before=now,
        )
        queued = 0
        for review in reviews:
            if self._store.queue_if_needed(review) and self._store.mark_queued(review.id):
                await self._queue.put(review)
                queued += 1
        while batch := self._store.due_reviews():
            for review in batch:
                if self._store.mark_queued(review.id):
                    await self._queue.put(review)
                    queued += 1
        await self._queue.join()
        summary = self._store.summary()
        pending = sum(summary.values()) - summary.get("completed", 0)
        self._status.set_reconciliation_result(
            discovered=len(reviews), queued=queued, pending=pending
        )
        state_summary = ", ".join(
            f"{state}={count}" for state, count in sorted(summary.items())
        ) or "empty"
        if pending == 0:
            logger.info(
                "Reconciliation successful: discovered %d review(s), processed %d, queue empty",
                len(reviews),
                queued,
            )
        else:
            logger.warning(
                "Reconciliation finished with pending reviews: discovered %d, newly queued %d, pending %d (%s)",
                len(reviews),
                queued,
                pending,
                state_summary,
            )

    async def run(self) -> None:
        self._status.configure_reconciliation(enabled=self._config.enabled)
        if not self._config.enabled:
            return
        first_run = True
        while True:
            try:
                if not first_run:
                    try:
                        await asyncio.wait_for(
                            self._trigger.wait(),
                            timeout=self._config.interval_minutes * 60,
                        )
                    except TimeoutError:
                        pass
                    self._trigger.clear()
                first_run = False
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                summary = self._store.summary()
                self._status.set_reconciliation_result(
                    discovered=0,
                    queued=0,
                    pending=sum(summary.values()) - summary.get("completed", 0),
                    error=str(exc),
                )
                logger.exception("Review reconciliation failed")
