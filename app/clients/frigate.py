from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

import aiohttp

from app.models.config import FrigateConfig
from app.models.review import Review

logger = logging.getLogger("frigate")


class FrigateClient:
    """
    Client responsible for all communication with Frigate.
    """

    def __init__(self, config: FrigateConfig):
        self._config = config
        self._session: aiohttp.ClientSession | None = None

        # Prevent multiple workers from authenticating simultaneously.
        self._login_lock = asyncio.Lock()
        self._connection_callback: (
            Callable[[str, str | None], None] | None
        ) = None

    def set_connection_callback(
        self,
        callback: Callable[[str, str | None], None],
    ) -> None:
        self._connection_callback = callback

    def _notify_connection(
        self,
        state: str,
        error: str | None = None,
    ) -> None:
        if self._connection_callback is not None:
            self._connection_callback(state, error)

    async def connect(self) -> None:
        logger.debug(
            "Connecting to Frigate at %s",
            self._config.url,
        )

        timeout = aiohttp.ClientTimeout(
            total=self._config.timeout
        )

        connector = aiohttp.TCPConnector(
            ssl=self._config.verify_ssl
        )

        cookie_jar = aiohttp.CookieJar(
            unsafe=True
        )

        self._session = aiohttp.ClientSession(
            base_url=self._config.url,
            timeout=timeout,
            connector=connector,
            cookie_jar=cookie_jar,
        )

        logger.debug("HTTP session created")

    async def login(self) -> None:
        logger.debug(
            "Authenticating as '%s'",
            self._config.username,
        )

        response = await self._session.post(
            "/api/login",
            json={
                "user": self._config.username,
                "password": self._config.password,
            },
        )

        if response.status != 200:
            raise RuntimeError(
                f"Authentication failed ({response.status})"
            )

        logger.debug("Authentication successful")

    async def _reconnect(self) -> None:
        logger.info(
            "Reconnecting to Frigate..."
        )

        self._notify_connection("reconnecting")

        try:
            if self._session is not None:
                await self._session.close()
                self._session = None

            await self.connect()

            async with self._login_lock:
                await self.login()
        except Exception as exc:
            self._notify_connection("disconnected", str(exc))
            raise

        logger.info(
            "Reconnected to Frigate."
        )
        self._notify_connection("connected")

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> aiohttp.ClientResponse:

        if self._session is None:
            raise RuntimeError(
                "HTTP session has not been created."
            )

        async def perform_request() -> aiohttp.ClientResponse:
            return await self._session.request(
                method,
                path,
                **kwargs,
            )

        try:
            response = await perform_request()

        except aiohttp.ClientError as exc:
            logger.warning(
                "Connection to Frigate lost: %s",
                exc,
            )

            await self._reconnect()

            response = await perform_request()

        if response.status == 401:
            logger.warning(
                "Authentication expired. Re-authenticating..."
            )

            async with self._login_lock:
                response = await perform_request()

                if response.status != 401:
                    return response

                await self.login()

            logger.info(
                "Authentication successful. Retrying request."
            )

            response = await perform_request()

        if response.status in (502, 503, 504):
            logger.warning(
                "Frigate temporarily unavailable (%d). Reconnecting...",
                response.status,
            )

            await self._reconnect()

            response = await perform_request()

        if response.status >= 400:
            body = await response.text()

            logger.error(
                "HTTP %s %s -> %d\n%s",
                method,
                path,
                response.status,
                body,
            )

            raise RuntimeError(
                f"{method} {path} failed "
                f"({response.status}): {body}"
            )

        return response

    @staticmethod
    def _review_timestamp(value: object) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            return datetime.fromisoformat(value).timestamp()
        raise ValueError(f"Unsupported review timestamp: {value!r}")

    async def list_reviews(
        self,
        *,
        after: float,
        before: float,
    ) -> list[Review]:
        response = await self._request(
            "GET",
            "/api/review",
            params={
                "after": int(after),
                "before": int(before),
                "limit": 1000,
            },
        )
        payload = await response.json()
        reviews = []
        for item in payload:
            if not item.get("end_time"):
                continue
            item = dict(item)
            item["start_time"] = self._review_timestamp(item["start_time"])
            item["end_time"] = self._review_timestamp(item["end_time"])
            reviews.append(Review.model_validate(item))
        return reviews

    async def version(self) -> str:
        response = await self._request(
            "GET",
            "/api/version",
        )

        return (await response.text()).strip()

    async def start_export(
        self,
        camera: str,
        start_time: float,
        end_time: float,
    ) -> dict:
        logger.debug(
            "Starting export: camera=%s start=%s end=%s",
            camera,
            start_time,
            end_time,
        )

        response = await self._request(
            "POST",
            f"/api/export/{camera}/start/{start_time}/end/{end_time}",
            json={
                "playback": "realtime",
            },
        )

        return await response.json()

    async def list_exports(self) -> list[dict]:
        response = await self._request(
            "GET",
            "/api/exports",
        )

        return await response.json()

    async def delete_exports(
        self,
        export_ids: list[str],
    ) -> None:
        logger.debug(
            "Deleting %d export(s) from Frigate...",
            len(export_ids),
        )

        response = await self._request(
            "POST",
            "/api/exports/delete",
            json={
                "ids": export_ids,
            },
        )

        result = await response.json()

        logger.debug(
            "Frigate cleanup complete: %s",
            result.get("message", "OK"),
        )

    async def disconnect(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

            logger.debug("HTTP session closed")

        self._notify_connection("disconnected")
