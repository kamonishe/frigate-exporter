import unittest
from unittest.mock import AsyncMock

from app.clients.frigate import FrigateClient
from app.models.config import FrigateConfig


class Response:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload

    def release(self):
        pass


def item(review_id: str, start: float) -> dict:
    return {
        "id": review_id,
        "camera": "camera1",
        "start_time": start,
        "end_time": start + 1,
        "severity": "alert",
        "thumb_path": "",
    }


class FrigateClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_reviews_paginates_and_deduplicates(self) -> None:
        client = FrigateClient(
            FrigateConfig(
                url="https://frigate",
                username="user",
                password="password",
            )
        )
        first_page = [item(f"r{i}", 2000 - i) for i in range(1000)]
        second_page = [item("older", 900)]
        client._request = AsyncMock(
            side_effect=[Response(first_page), Response(second_page)]
        )

        reviews = await client.list_reviews(after=0, before=3000)

        self.assertEqual(len(reviews), 1001)
        self.assertEqual(client._request.await_count, 2)
        second_params = client._request.await_args_list[1].kwargs["params"]
        self.assertLessEqual(second_params["before"], 1000)


if __name__ == "__main__":
    unittest.main()
