from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import aiomqtt

from app.models.config import MQTTConfig

logger = logging.getLogger("frigate_exporter.mqtt")

RECONNECT_DELAY = 5


class MQTTClient:
    """
    Client responsible for all MQTT communication.
    """

    def __init__(self, config: MQTTConfig):
        self._config = config
        self._client: aiomqtt.Client | None = None
        self._callback: Callable[[aiomqtt.Message], Awaitable[None]] | None = None
        self._stop_requested = False

    def set_message_callback(
        self,
        callback: Callable[[aiomqtt.Message], Awaitable[None]],
    ) -> None:
        """
        Register a callback invoked for every MQTT message.
        """
        self._callback = callback

    async def connect(self) -> None:
        if self._client is not None:
            return

        logger.debug(
            "Connecting to MQTT broker %s:%s",
            self._config.host,
            self._config.port,
        )

        client = aiomqtt.Client(
            hostname=self._config.host,
            port=self._config.port,
            username=self._config.username,
            password=self._config.password,
        )

        await client.__aenter__()
        self._client = client

        logger.info(
            "Connected to MQTT broker %s:%s",
            self._config.host,
            self._config.port,
        )

    async def subscribe(self) -> None:
        if self._client is None:
            raise RuntimeError("MQTT client has not been connected.")

        logger.debug(
            "Subscribing to '%s'",
            self._config.topic,
        )

        await self._client.subscribe(self._config.topic)

        logger.info(
            "Subscribed to '%s'",
            self._config.topic,
        )

    async def _close_client(self) -> None:
        client = self._client
        self._client = None

        if client is None:
            return

        try:
            await client.__aexit__(None, None, None)
        except Exception:
            logger.debug(
                "Error while closing MQTT connection",
                exc_info=True,
            )

    async def listen(self) -> None:
        """
        Listen forever and reconnect automatically after MQTT failures.
        """

        if self._callback is None:
            raise RuntimeError(
                "No MQTT message callback has been registered."
            )

        logger.debug("Starting MQTT listener")

        while not self._stop_requested:
            try:
                await self.connect()
                await self.subscribe()

                client = self._client

                if client is None:
                    raise RuntimeError(
                        "MQTT client disappeared after connecting."
                    )

                async for message in client.messages:
                    await self._callback(message)

            except asyncio.CancelledError:
                raise

            except (aiomqtt.MqttError, OSError, TimeoutError) as exc:
                if self._stop_requested:
                    break

                logger.warning(
                    "MQTT connection lost: %s. "
                    "Reconnecting in %d seconds...",
                    exc,
                    RECONNECT_DELAY,
                )

                await self._close_client()
                await asyncio.sleep(RECONNECT_DELAY)

            except Exception:
                await self._close_client()
                raise

    async def disconnect(self) -> None:
        self._stop_requested = True
        await self._close_client()

        logger.debug("Disconnected from MQTT broker")
