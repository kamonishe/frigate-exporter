from __future__ import annotations

import logging

import aiohttp

from app.models.config import FrigateConfig

logger = logging.getLogger("frigate")


class FrigateClient:
    """
    Client responsible for all communication with Frigate.
    """

    def __init__(self, config: FrigateConfig):
        self._config = config
        self._session: aiohttp.ClientSession | None = None

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

        response = await self._session.request(
            method,
            path,
            **kwargs,
        )

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