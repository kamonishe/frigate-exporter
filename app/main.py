import asyncio
import json
import logging
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
from app.services.runtime_status import RuntimeStatus
from app.services.storage import summarize_export_storage
from app.workers.export_worker import ExportWorker
from app.workers.retention_worker import RetentionWorker

logger = logging.getLogger("exporter")

FRIGATE_CONNECT_RETRIES = 60
FRIGATE_CONNECT_RETRY_DELAY = 5

review_queue = ReviewQueue()


class StartupError(RuntimeError):
    """Raised when the exporter cannot start."""


async def handle_review_message(
    message: aiomqtt.Message,
    review_filter: ReviewFilter,
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

    await review_queue.put(review)

    logger.debug(
        "Queue size: %d",
        review_queue.size(),
    )


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
    dashboard = DashboardServer(cfg.dashboard, status, review_queue)

    mqtt.set_connection_callback(status.set_mqtt_state)
    frigate.set_connection_callback(status.set_frigate_state)

    mqtt.set_message_callback(
        lambda message: handle_review_message(
            message,
            review_filter,
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
                ).run(),
                name=f"export-worker-{i + 1}",
            )
            for i in range(cfg.export.workers)
        ]

        retention_task = asyncio.create_task(
            RetentionWorker(cfg, status).run(),
            name="retention-worker",
        )

        await mqtt.listen()

        await asyncio.gather(
            *worker_tasks,
            retention_task,
        )

    finally:
        await dashboard.stop()
        await mqtt.disconnect()
        await frigate.disconnect()

        logger.debug("Shutdown complete")


def main() -> None:
    try:
        asyncio.run(async_main())

    except StartupError as exc:
        logger.error(str(exc))
        logger.error("Exiting.")
        sys.exit(1)


if __name__ == "__main__":
    main()
