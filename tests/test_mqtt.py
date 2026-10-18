import unittest
from unittest.mock import AsyncMock

from app.clients.mqtt import MQTTClient
from app.models.config import MQTTConfig


class MQTTClientTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self) -> MQTTClient:
        return MQTTClient(
            MQTTConfig(
                host="mqtt",
                port=1883,
                username="user",
                password="password",
                topic="frigate/reviews",
            )
        )

    async def test_check_connection_confirms_broker_subscription(self) -> None:
        client = self.make_client()
        broker = AsyncMock()
        client._client = broker

        await client.check_connection()

        broker.subscribe.assert_awaited_once_with(
            "frigate/reviews",
            timeout=10,
        )

    async def test_request_reconnect_closes_client_and_notifies(self) -> None:
        client = self.make_client()
        broker = AsyncMock()
        client._client = broker
        states: list[tuple[str, str | None]] = []
        client.set_connection_callback(
            lambda state, error=None: states.append((state, error))
        )

        await client.request_reconnect("health timeout")

        broker.__aexit__.assert_awaited_once_with(None, None, None)
        self.assertEqual(states, [("reconnecting", "health timeout")])


if __name__ == "__main__":
    unittest.main()
