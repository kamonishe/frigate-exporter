import base64
import json
import logging
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from app.core.logger import HTTPLogFilter, change_logging, logging_settings
from app.models.config import DashboardConfig
from app.queue.review_queue import ReviewQueue
from app.services.dashboard import DashboardServer
from app.services.runtime_status import RuntimeStatus


class LoggingControlsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous = logging_settings()
        self.dashboard = DashboardServer(
            DashboardConfig(username="viewer", password="secret"),
            RuntimeStatus("reviews", 1), ReviewQueue(),
        )
        app = web.Application()
        app.router.add_post("/api/logging", self.dashboard._logging_response)
        app.router.add_get("/", self.dashboard._index)
        app.router.add_get("/login", self.dashboard._login_page)
        app.router.add_post("/api/login", self.dashboard._login)
        app.router.add_post("/api/logout", self.dashboard._logout)
        app.router.add_get("/api/status", self.dashboard._status_response)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        logging.getLogger().setLevel(self.previous["level"])
        import app.core.logger as module
        module._http_logs = self.previous["http_logs"]
        await self.client.close()

    async def test_authenticated_logging_update_and_validation(self):
        change_logging("INFO", False)
        payload = {"level": "DEBUG", "http_logs": True}
        response = await self.client.post("/api/logging", json=payload)
        self.assertEqual(response.status, 401)
        auth = {"Authorization": "Basic " + base64.b64encode(b"viewer:secret").decode()}
        response = await self.client.post("/api/logging", json=payload, headers=auth)
        self.assertEqual(response.status, 403)
        headers = {**auth, "X-Exporter-Request": "dashboard"}
        response = await self.client.post(
            "/api/logging", json=payload, headers=headers,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), payload)
        for invalid in [None, {"level": "NOPE", "http_logs": True},
                        {"level": "INFO", "http_logs": "false"}]:
            response = await self.client.post(
                "/api/logging", data=json.dumps(invalid),
                headers={**headers, "Content-Type": "application/json"},
            )
            self.assertEqual(response.status, 400)
            self.assertEqual(logging_settings(), payload)

    def test_http_filter_blocks_noise_without_blocking_exporter(self):
        log_filter = HTTPLogFilter()
        http = logging.LogRecord("aiohttp.access", logging.INFO, "", 0, "poll", (), None)
        exporter = logging.LogRecord("exporter", logging.INFO, "", 0, "export", (), None)
        change_logging("INFO", False)
        self.assertFalse(log_filter.filter(http))
        self.assertTrue(log_filter.filter(exporter))
        change_logging("DEBUG", True)
        self.assertTrue(log_filter.filter(http))
        self.assertTrue(logging.getLogger("exporter").isEnabledFor(logging.DEBUG))
        change_logging("ERROR", False)
        self.assertFalse(logging.getLogger("exporter").isEnabledFor(logging.INFO))

    async def test_session_login_logout_expiry_and_log_filtering(self):
        response = await self.client.get("/", allow_redirects=False)
        self.assertEqual(response.status, 302)
        self.assertEqual(response.headers["Location"], "/login")
        response = await self.client.get("/login")
        self.assertIn('id="login-form"', await response.text())
        self.assertNotIn("WWW-Authenticate", response.headers)
        headers = {"X-Exporter-Request": "dashboard"}
        response = await self.client.post("/api/login", headers=headers,
                                          json={"username": "viewer", "password": "wrong"})
        self.assertEqual(response.status, 401)
        self.assertNotIn("WWW-Authenticate", response.headers)
        response = await self.client.post("/api/login", headers=headers,
                                          json={"username": "viewer", "password": "secret"})
        self.assertEqual(response.status, 200)
        cookie = response.cookies["exporter_session"]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        response = await self.client.get("/")
        self.assertIn('id="logout"', await response.text())
        self.dashboard._status.add_log("DEBUG", "exporter", "debug entry")
        self.dashboard._status.add_log("ERROR", "exporter", "error entry")
        self.dashboard._status.add_log("ERROR", "aiohttp.access", "http entry")
        response = await self.client.post("/api/logging", headers=headers,
                                          json={"level": "ERROR", "http_logs": False})
        self.assertEqual(response.status, 200)
        response = await self.client.get("/api/status")
        self.assertEqual([entry["message"] for entry in (await response.json())["logs"]], ["error entry"])
        response = await self.client.post("/api/logout", headers=headers)
        self.assertEqual(response.status, 200)
        response = await self.client.get("/api/status", headers={"Cookie": "exporter_session=" + cookie.value})
        self.assertEqual(response.status, 401)
        self.dashboard._sessions["expired"] = 0
        response = await self.client.get("/api/status", headers={"Cookie": "exporter_session=expired"})
        self.assertEqual(response.status, 401)
