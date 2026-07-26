from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import aiomqtt

from app.models.config import MQTTConfig

logger = logging.getLogger("frigate_exporter.mqtt")


class MQTTClient:
    """
    Client responsible for all MQTT communication.
    """

    def __init__(self, config: MQTTConfig):
        self._config = config
        self._client: aiomqtt.Client | None = None
        self._callback: Callable[[aiomqtt.Message], Awaitable[None]] | None = None

    def set_message_callback(
        self,
        callback: Callable[[aiomqtt.Message], Awaitable[None]],
    ) -> None:
        """
        Register a callback invoked for every MQTT message.
        """
        self._callback = callback

    async def connect(self) -> None:
        logger.debug(
            "Connecting to MQTT broker %s:%s",
            self._config.host,
            self._config.port,
        )

        self._client = aiomqtt.Client(
            hostname=self._config.host,
            port=self._config.port,
            username=self._config.username,
            password=self._config.password,
        )

        await self._client.__aenter__()

        logger.debug("Connected to MQTT broker")

    async def subscribe(self) -> None:
        if self._client is None:
            raise RuntimeError("MQTT client has not been connected.")

        logger.debug(
            "Subscribing to '%s'",
            self._config.topic,
        )

        await self._client.subscribe(self._config.topic)

        logger.debug("Subscription successful")

    async def listen(self) -> None:
        """
        Listen forever and forward every message to the callback.
        """

        if self._client is None:
            raise RuntimeError("MQTT client has not been connected.")

        if self._callback is None:
            raise RuntimeError(
                "No MQTT message callback has been registered."
            )

        logger.debug("Listening for MQTT messages...")

        async for message in self._client.messages:
            await self._callback(message)

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)
            finally:
                self._client = None

            logger.debug("Disconnected from MQTT broker")
