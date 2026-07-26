import asyncio
import json
import logging

import aiomqtt

from app.clients.frigate import FrigateClient
from app.clients.mqtt import MQTTClient
from app.core.config import load_config
from app.core.logger import setup_logging
from app.core.version import APP_VERSION
from app.models.review import Review
from app.queue.review_queue import ReviewQueue
from app.workers.export_worker import ExportWorker
from app.workers.retention_worker import RetentionWorker

logger = logging.getLogger("exporter")

review_queue = ReviewQueue()


async def handle_review_message(message: aiomqtt.Message) -> None:
    try:
        payload = json.loads(message.payload.decode())

    except Exception:
        logger.exception("Failed to decode MQTT payload")
        return

    #
    # We only care about completed reviews.
    #
    if payload.get("type") != "end":
        return

    review = Review.model_validate(payload["after"])

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

    setup_logging(cfg.logging.level)

    frigate = FrigateClient(cfg.frigate)
    mqtt = MQTTClient(cfg.mqtt)

    mqtt.set_message_callback(handle_review_message)

    try:
        #
        # Connect to Frigate.
        #
        await frigate.connect()
        await frigate.login()

        frigate_version = await frigate.version()

        #
        # Connect to MQTT.
        #
        await mqtt.connect()
        await mqtt.subscribe()

        #
        # Always display startup summary.
        #
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

        print(f"{'Logging':<12}: {cfg.logging.level.upper()}")
        print("=" * 60)
        print("Ready. Waiting for completed reviews...")
        print()

        worker_tasks = [
            asyncio.create_task(
                ExportWorker(
                    worker_id=i + 1,
                    queue=review_queue,
                    frigate=frigate,
                    config=cfg,
                ).run(),
                name=f"export-worker-{i + 1}",
            )
            for i in range(cfg.export.workers)
        ]

        retention_task = asyncio.create_task(
            RetentionWorker(cfg).run(),
            name="retention-worker",
        )

        await mqtt.listen()

        await asyncio.gather(
            *worker_tasks,
            retention_task,
        )

    finally:
        await mqtt.disconnect()
        await frigate.disconnect()

        logger.debug("Shutdown complete")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()