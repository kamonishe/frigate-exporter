from __future__ import annotations

import asyncio

from app.models.review import Review


class ReviewQueue:
    """
    Queue of completed Frigate reviews waiting to be exported.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Review] = asyncio.Queue()

    async def put(self, review: Review) -> None:
        """
        Add a review to the queue.
        """
        await self._queue.put(review)

    async def get(self) -> Review:
        """
        Wait for the next review.
        """
        return await self._queue.get()

    async def join(self) -> None:
        """Wait until all queued reviews have been processed."""
        await self._queue.join()

    def task_done(self) -> None:
        """
        Mark the current review as processed.
        """
        self._queue.task_done()

    def size(self) -> int:
        """
        Return the number of queued reviews.
        """
        return self._queue.qsize()
