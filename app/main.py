import asyncio
import json
import logging
import signal
import sys
from pathlib import Path

import aiomqtt

from app.clients.frigate import FrigateClient
from app.clients.mqtt import MQTTClient
from app.core.config import load_config
from app.core.logger import setup_logging
from app.core.version import APP_VERSION
from app.models.review import Review
from app.queue.review_queue import ReviewQueue
from app.services.dashboard import DashboardServer
from app.services.review_filter import ReviewFilter
from app.services.review_store import ReviewStore
from app.services.runtime_status import RuntimeStatus
from app.services.storage import summarize_export_storage
from app.workers.export_worker import ExportWorker
from app.workers.reconciliation_worker import ReconciliationWorker
from app.workers.retention_worker import RetentionWorker

logger = logging.getLogger("exporter")

FRIGATE_CONNECT_RETRIES = 60
FRIGATE_CONNECT_RETRY_DELAY = 5
FRIGATE_HEALTH_INTERVAL = 15

review_queue = ReviewQueue()


class StartupError(RuntimeError):
    """Raised when the exporter cannot start."""


async def handle_review_message(
    message: aiomqtt.Message,
    review_filter: ReviewFilter,
    store: ReviewStore | None = None,
) -> None:
    try:
        payload = json.loads(message.payload.decode())

    except Exception:
        logger.exception("Failed to decode MQTT payload")
        return

    if payload.get("type") != "end":
        return

    try:
        review = Review.model_validate(payload["after"])
    except Exception:
        logger.exception("Failed to validate Frigate review payload")
        return

    if not review_filter.matches(review):
        logger.debug(
            "Filtered review %s from camera %s",
            review.id,
            review.camera,
        )
        return

    logger.debug(
        "Queued review %s from camera %s",
        review.id,
        review.camera,
    )

    if store is not None and (
        not store.queue_if_needed(review) or not store.mark_queued(review.id)
    ):
        logger.debug("Review %s is already tracked", review.id)
        return

    await review_queue.put(review)

    logger.debug(
        "Queue size: %d",
        review_queue.size(),
    )


async def monitor_frigate(
    frigate: FrigateClient,
    status: RuntimeStatus,
) -> None:
    """Keep probing Frigate so restarts recover without a worker request."""
    while True:
        try:
            version = await frigate.version()
            status.set_frigate_connected(version)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            status.set_frigate_state("disconnected", str(exc))
            logger.warning("Frigate health check failed: %s", exc)
        await asyncio.sleep(FRIGATE_HEALTH_INTERVAL)


async def async_main() -> None:
    cfg = load_config()

    status = RuntimeStatus(
        cfg.mqtt.topic,
        cfg.export.workers,
        secrets=[
            cfg.frigate.username,
            cfg.frigate.password,
            cfg.mqtt.username,
            cfg.mqtt.password,
            cfg.dashboard.username or "",
            cfg.dashboard.password or "",
        ],
        history_path=Path(cfg.export.output) / ".frigate-exporter-dashboard-history.json",
    )
    status.configure_retention(
        enabled=cfg.retention.enabled,
        days=cfg.retention.days,
        check_interval_hours=cfg.retention.check_interval_hours,
    )

    setup_logging(cfg.logging.level, status.add_log)

    frigate = FrigateClient(cfg.frigate)
    mqtt = MQTTClient(cfg.mqtt)
    review_filter = ReviewFilter(cfg.filters)
    review_store = ReviewStore(Path(cfg.export.output))
    review_store.initialize()
    dashboard = DashboardServer(cfg.dashboard, status, review_queue)
    reconciliation_worker = ReconciliationWorker(
        cfg.reconciliation, frigate, review_queue, review_store, status
    )

    def on_mqtt_state(state: str, error: str | None = None) -> None:
        status.set_mqtt_state(state, error)
        if state == "connected":
            reconciliation_worker.trigger_reconcile("MQTT reconnect")

    def on_frigate_state(state: str, error: str | None = None) -> None:
        status.set_frigate_state(state, error)
        if state == "connected":
            reconciliation_worker.trigger_reconcile("Frigate reconnect")

    mqtt.set_connection_callback(on_mqtt_state)
    frigate.set_connection_callback(on_frigate_state)

    mqtt.set_message_callback(
        lambda message: handle_review_message(
            message,
            review_filter,
            review_store,
        )
    )

    try:
        if cfg.dashboard.enabled:
            if not cfg.dashboard.username or not cfg.dashboard.password:
                raise StartupError(
                    "Dashboard authentication requires "
                    "DASHBOARD_USERNAME and DASHBOARD_PASSWORD."
                )
            await dashboard.start()
            logger.info(
                "Dashboard available at http://%s:%d",
                cfg.dashboard.host,
                cfg.dashboard.port,
            )

        await frigate.connect()

        logger.info(
            "Waiting for Frigate to become available..."
        )

        for attempt in range(1, FRIGATE_CONNECT_RETRIES + 1):
            try:
                await frigate.login()
                frigate_version = await frigate.version()

                logger.info("Connected to Frigate.")
                status.set_frigate_connected(frigate_version)

                break

            except Exception as exc:  # noqa: BLE001
                status.set_frigate_state("disconnected", str(exc))
                logger.debug(
                    "Connection attempt %d/%d failed: %s",
                    attempt,
                    FRIGATE_CONNECT_RETRIES,
                    exc,
                )

                if attempt == FRIGATE_CONNECT_RETRIES:
                    raise StartupError(
                        f"Unable to connect to Frigate after "
                        f"{FRIGATE_CONNECT_RETRIES} attempts."
                    ) from None

                await asyncio.sleep(
                    FRIGATE_CONNECT_RETRY_DELAY
                )

        print()
        print("=" * 60)
        print("               Frigate Exporter")
        print("=" * 60)
        print(f"{'Exporter':<12}: {APP_VERSION}")
        print(f"{'Frigate':<12}: {frigate_version}")
        print(f"{'URL':<12}: {cfg.frigate.url}")
        print(f"{'MQTT':<12}: {cfg.mqtt.host}:{cfg.mqtt.port}")
        print(f"{'Workers':<12}: {cfg.export.workers}")
        print(f"{'Output':<12}: {cfg.export.output}")
        print(
            f"{'Capture':<12}: "
            f"-{cfg.export.pre_capture}s / +{cfg.export.post_capture}s"
        )

        if cfg.retention.enabled:
            print(
                f"{'Retention':<12}: "
                f"{cfg.retention.days} day(s), "
                f"every {cfg.retention.check_interval_hours} hour(s)"
            )
        else:
            print(f"{'Retention':<12}: Disabled")

        print(
            f"{'Filters':<12}: "
            f"cameras={len(cfg.filters.cameras) or 'all'}, "
            f"labels={len(cfg.filters.labels) or 'all'}, "
            f"severity={', '.join(cfg.filters.severity) or 'all'}"
        )
        print(f"{'Logging':<12}: {cfg.logging.level.upper()}")
        print("=" * 60)
        print("Ready. Waiting for completed reviews...")
        print()

        status.set_storage(
            await asyncio.to_thread(
                summarize_export_storage,
                Path(cfg.export.output),
            )
        )

        worker_tasks = [
            asyncio.create_task(
                ExportWorker(
                    worker_id=i + 1,
                    queue=review_queue,
                    frigate=frigate,
                    config=cfg,
                    status=status,
                    store=review_store,
                ).run(),
                name=f"export-worker-{i + 1}",
            )
            for i in range(cfg.export.workers)
        ]

        retention_task = asyncio.create_task(
            RetentionWorker(cfg, status).run(),
            name="retention-worker",
        )
        frigate_monitor_task = asyncio.create_task(
            monitor_frigate(frigate, status),
            name="frigate-health-monitor",
        )
        reconciliation_task = asyncio.create_task(
            reconciliation_worker.run(),
            name="review-reconciliation",
        )

        await mqtt.listen()

        await asyncio.gather(
            *worker_tasks,
            retention_task,
        )

    finally:
        tasks = list(locals().get("worker_tasks", []))
        retention = locals().get("retention_task")
        if retention is not None:
            tasks.append(retention)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        monitor_task = locals().get("frigate_monitor_task")
        if monitor_task is not None:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
        reconciliation_task = locals().get("reconciliation_task")
        if reconciliation_task is not None:
            reconciliation_task.cancel()
            await asyncio.gather(reconciliation_task, return_exceptions=True)
        await dashboard.stop()
        await mqtt.disconnect()
        await frigate.disconnect()

        logger.debug("Shutdown complete")


async def run_until_stopped() -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(async_main())
    stopping = False

    def stop() -> None:
        nonlocal stopping
        if not stopping:
            stopping = True
            task.cancel()

    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop)
    try:
        await task
    except asyncio.CancelledError:
        if not stopping:
            raise
    finally:
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)


def main() -> None:
    try:
        asyncio.run(run_until_stopped())

    except StartupError as exc:
        logger.error(str(exc))
        logger.error("Exiting.")
        sys.exit(1)


if __name__ == "__main__":
    main()
