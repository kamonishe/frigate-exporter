from __future__ import annotations

import base64
import binascii
import hmac
import logging
import secrets
import time
from pathlib import Path

from aiohttp import web

from app.core.logger import change_logging, logging_settings
from app.models.config import DashboardConfig
from app.queue.review_queue import ReviewQueue
from app.services.runtime_status import RuntimeStatus


class DashboardServer:
    """Authenticated runtime dashboard with temporary logging controls."""

    def __init__(
        self,
        config: DashboardConfig,
        status: RuntimeStatus,
        queue: ReviewQueue,
    ) -> None:
        self._config = config
        self._status = status
        self._queue = queue
        self._runner: web.AppRunner | None = None
        self._sessions: dict[str, float] = {}

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/api/status", self._status_response)
        app.router.add_get("/healthz", self._health)
        app.router.add_post("/api/logging", self._logging_response)
        app.router.add_get("/login", self._login_page)
        app.router.add_post("/api/login", self._login)
        app.router.add_post("/api/logout", self._logout)
        app.router.add_get("/logo.png", self._logo)

        self._runner = web.AppRunner(app)
        await self._runner.setup()

        site = web.TCPSite(
            self._runner,
            host=self._config.host,
            port=self._config.port,
        )
        await site.start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _logo(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(
            Path(__file__).resolve().parent.parent / "static" / "clip-courier-white.png"
        )

    def _authorized(self, request: web.Request) -> bool:
        if self._session_authorized(request):
            return True
        header = request.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False

        try:
            decoded = base64.b64decode(
                header.removeprefix("Basic "),
                validate=True,
            ).decode("utf-8")
            username, password = decoded.split(":", 1)
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return False

        return self._credentials_match(username, password)

    def _credentials_match(self, username: str, password: str) -> bool:
        expected_username = self._config.username or ""
        expected_password = self._config.password or ""
        return bool(expected_username and expected_password) and (
            hmac.compare_digest(username.encode(), expected_username.encode())
            and hmac.compare_digest(password.encode(), expected_password.encode())
        )

    def _require_auth(self, request: web.Request) -> None:
        if not self._authorized(request):
            raise web.HTTPUnauthorized()

    def _session_authorized(self, request: web.Request) -> bool:
        token = getattr(request, "cookies", {}).get("exporter_session", "")
        return self._sessions.get(token, 0) > time.monotonic()

    @staticmethod
    def _require_dashboard_request(request: web.Request) -> None:
        if request.headers.get("X-Exporter-Request") != "dashboard":
            raise web.HTTPForbidden()

    async def _login_page(self, request: web.Request) -> web.Response:
        if self._session_authorized(request):
            raise web.HTTPFound("/")
        return web.Response(text=_LOGIN_HTML, content_type="text/html",
                            headers={"Cache-Control": "no-store"})

    async def _login(self, request: web.Request) -> web.Response:
        self._require_dashboard_request(request)
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        try:
            credentials = await request.json()
        except ValueError:
            raise web.HTTPBadRequest() from None
        if not isinstance(credentials, dict):
            raise web.HTTPBadRequest()
        username, password = credentials.get("username"), credentials.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise web.HTTPBadRequest()
        if not self._credentials_match(username, password):
            raise web.HTTPUnauthorized(text="Invalid username or password")
        now = time.monotonic()
        self._sessions = {token: expiry for token, expiry in self._sessions.items() if expiry > now}
        self._sessions.pop(request.cookies.get("exporter_session", ""), None)
        if len(self._sessions) >= 100:
            self._sessions.pop(next(iter(self._sessions)))
        token = secrets.token_urlsafe(32)
        self._sessions[token] = now + 12 * 3600
        response = web.json_response({"ok": True}, headers={"Cache-Control": "no-store"})
        response.set_cookie("exporter_session", token, max_age=12 * 3600,
                            httponly=True, samesite="Strict", secure=request.secure, path="/")
        return response

    async def _logout(self, request: web.Request) -> web.Response:
        self._require_dashboard_request(request)
        self._sessions.pop(request.cookies.get("exporter_session", ""), None)
        response = web.json_response({"ok": True})
        response.del_cookie("exporter_session", path="/")
        return response

    async def _status_response(self, request: web.Request) -> web.Response:
        self._require_auth(request)
        payload = self._status.snapshot(self._queue.size())
        payload["logging"] = logging_settings()
        # Apply the current selection to retained entries as well as future output.
        level = logging.getLogger().level
        payload["logs"] = [entry for entry in payload["logs"]
                           if getattr(logging, entry["level"], 0) >= level
                           and (payload["logging"]["http_logs"] or not (
                               entry["logger"] == "aiohttp" or entry["logger"].startswith("aiohttp.")))]
        return web.json_response(payload, headers={"Cache-Control": "no-store"})

    async def _logging_response(self, request: web.Request) -> web.Response:
        self._require_auth(request)
        # Non-simple header prevents cross-origin form submissions.
        if request.headers.get("X-Exporter-Request") != "dashboard":
            raise web.HTTPForbidden()
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        try:
            settings = await request.json()
            if not isinstance(settings, dict) or set(settings) != {"level", "http_logs"}:
                raise ValueError()
            if not isinstance(settings["level"], str):
                raise TypeError()
            change_logging(settings["level"], settings["http_logs"])
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(text="Invalid logging settings") from None
        return web.json_response(logging_settings())

    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _index(self, request: web.Request) -> web.Response:
        if not self._session_authorized(request):
            raise web.HTTPFound("/login")
        return web.Response(text=_DASHBOARD_HTML, content_type="text/html",
                            headers={"Cache-Control": "no-store"})


_LOGIN_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in · Frigate Exporter</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #111827; color: #e5e7eb; }
    main { background: #1f2937; border-radius: 1rem; padding: 2rem; width: min(360px, calc(100vw - 6rem)); }
    h1 { font-size: 1.6rem; margin-top: 0; } p { color: #9ca3af; }
    label { display: block; margin: 1rem 0 .4rem; }
    input, button { box-sizing: border-box; width: 100%; padding: .8rem; border-radius: .4rem; font: inherit; }
    input { border: 1px solid #6b7280; background: #111827; color: #e5e7eb; }
    button { border: 0; background: #34d399; color: #111827; margin-top: 1.4rem; cursor: pointer; }
    #error { color: #f87171; min-height: 1.5rem; }
  </style>
</head>
<body><main>
  <img src="/logo.png" alt="Clip Courier pigeon carrying a video envelope" width="112" height="112" style="display:block;margin:0 auto 1.5rem">
  <h1>Frigate Exporter</h1><p>Sign in to your dashboard.</p>
  <form id="login-form">
    <label for="username">Username</label><input id="username" name="username" autocomplete="username" required autofocus>
    <label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required>
    <button id="sign-in" type="submit">Sign in</button>
    <p id="error" role="alert"></p>
  </form>
</main><script>
  document.getElementById('login-form').addEventListener('submit', async event => {
    event.preventDefault();
    const button = document.getElementById('sign-in'); button.disabled = true;
    const error = document.getElementById('error'); error.textContent = '';
    try {
      const response = await fetch('/api/login', {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Exporter-Request': 'dashboard'},
        body: JSON.stringify({username: document.getElementById('username').value, password: document.getElementById('password').value})
      });
      if (response.ok) { location.replace('/'); return; }
      error.textContent = response.status === 401 ? 'Invalid username or password.' : 'Unable to sign in. Please retry.';
    } catch (_) { error.textContent = 'Cannot reach the exporter. Please retry.'; }
    finally { button.disabled = false; document.getElementById('password').value = ''; }
  });
</script></body></html>"""


_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Frigate Exporter</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    body { background: #111827; color: #e5e7eb; margin: 0; }
    main { max-width: 1100px; margin: auto; padding: 2rem; }
    h1 { margin-bottom: .2rem; } h2 { margin-top: 2rem; }
    .muted, .detail { color: #9ca3af; }
    .detail { font-size: .85rem; line-height: 1.5; margin-top: .6rem; }
    .grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
    .card { background: #1f2937; border-radius: .75rem; padding: 1rem; }
    .wide { grid-column: 1 / -1; }
    .detail-row { display: grid; grid-template-columns: minmax(100px, .7fr) 1fr; gap: .8rem; padding: .55rem 0; margin: 0; border-top: 1px solid #374151; }
    dt { color: #9ca3af; } dd { margin: 0; overflow-wrap: anywhere; }
    .controls { display: flex; align-items: center; flex-wrap: wrap; gap: 1rem; }
    .workers-heading { display: flex; align-items: baseline; flex-wrap: wrap; gap: .75rem; margin-top: 2rem; }
    .workers-heading h2 { margin: 0; }
    .queue-summary { color: #9ca3af; font-size: .9rem; }
    select, button { background: #1f2937; color: #e5e7eb; padding: .5rem; border: 1px solid #6b7280; border-radius: .4rem; }
    #retention { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 0 2rem; }
    .value { font-size: 1.45rem; font-weight: 700; margin-top: .4rem; text-transform: capitalize; }
    .good { color: #34d399; } .bad { color: #f87171; } .warn { color: #fbbf24; }
    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
    th, td { border-bottom: 1px solid #374151; padding: .65rem; text-align: left; vertical-align: top; }
    .progress { background: #374151; border-radius: 999px; height: .65rem; min-width: 130px; overflow: hidden; margin-top: .35rem; }
    .progress > span { display: block; height: 100%; background: #34d399; transition: width .3s ease; }
    .progress.indeterminate > span { width: 35%; animation: slide 1.2s infinite ease-in-out; }
    .log-error { color: #f87171; } .log-warning { color: #fbbf24; }
    .logs td:last-child { overflow-wrap: anywhere; }
    @keyframes slide { from { transform: translateX(-110%); } to { transform: translateX(300%); } }
    @media (max-width: 600px) { main { padding: 1rem; } .wide { grid-column: span 1; } }
  </style>
</head>
<body>
  <main>
    <div style="display:flex;align-items:center;gap:1rem;flex-wrap:wrap">
      <img src="/logo.png" alt="Clip Courier pigeon carrying a video envelope" width="72" height="72">
      <h1>Frigate Exporter</h1>
    </div>
    <button type="button" id="logout">Sign out</button>
    <p class="muted" id="subtitle">Loading status…</p>
    <section class="grid">
      <article class="card"><div>Frigate</div><div class="value" id="frigate">—</div><div class="detail" id="frigate-detail"></div></article>
      <article class="card"><div>MQTT</div><div class="value" id="mqtt">—</div><div class="detail" id="mqtt-detail"></div></article>
      <article class="card wide"><div>Exported storage &amp; retention</div><div class="value" id="storage">—</div><div class="detail" id="retention"></div></article>
    </section>
    <div class="workers-heading"><h2>Workers</h2><span class="queue-summary" id="queue">Loading queue…</span></div>
    <div class="table-wrap"><table><thead><tr><th>Worker</th><th>Status</th><th>Activity</th><th>Progress</th><th>Elapsed</th><th>Started</th><th>Completed</th><th>Last result</th></tr></thead>
    <tbody id="workers"></tbody></table></div>
    <h2>Latest logs</h2>
    <div class="controls">
      <label>Log level <select id="log-level"><option>DEBUG</option><option selected>INFO</option><option>WARNING</option><option>ERROR</option><option>CRITICAL</option></select></label>
      <label><input type="checkbox" id="http-logs"> Enable HTTP logs (aiohttp)</label>
      <span id="logging-feedback" role="status"></span>
    </div>
    <p class="muted">Changes affect dashboard and container logs immediately and reset on restart. HTTP logs are disabled by default.</p>
    <div class="table-wrap"><table class="logs"><thead><tr><th>Time</th><th>Level</th><th>Logger</th><th>Message</th></tr></thead>
    <tbody id="logs"></tbody></table></div>
  </main>
  <script>
    const timestamp = value => value ? new Date(value).toLocaleString() : '—';
    const duration = value => value == null ? '—' : `${Number(value).toFixed(1)} s`;
    const stateClass = state => state === 'connected' ? 'good' : state === 'reconnecting' ? 'warn' : 'bad';
    const detailRows = (id, entries) => {
      document.getElementById(id).replaceChildren(...entries.map(([label, value]) => {
        const row = document.createElement('dl'); row.className = 'detail-row';
        const term = document.createElement('dt'); term.textContent = label;
        const detail = document.createElement('dd'); detail.textContent = value;
        row.append(term, detail); return row;
      }));
    };
    let loggingEdited = false;
    let loggingSaving = false;
    let settingsRevision = 0;
    document.getElementById('logout').addEventListener('click', async () => {
      const response = await fetch('/api/logout', {method: 'POST', headers: {'X-Exporter-Request': 'dashboard'}});
      if (response.ok) location.replace('/login');
    });
    async function saveLogging() {
      loggingEdited = true;
      loggingSaving = true;
      settingsRevision++;
      const controls = ['log-level', 'http-logs'].map(id => document.getElementById(id));
      controls.forEach(control => { control.disabled = true; });
      document.getElementById('logging-feedback').textContent = 'Saving…';
      try {
        const response = await fetch('/api/logging', {
          method: 'POST', headers: {'Content-Type': 'application/json', 'X-Exporter-Request': 'dashboard'},
          body: JSON.stringify({level: document.getElementById('log-level').value, http_logs: document.getElementById('http-logs').checked})
        });
        if (response.status === 401) { location.replace('/login'); return; }
        if (!response.ok) throw new Error();
        const saved = await response.json();
        document.getElementById('log-level').value = saved.level;
        document.getElementById('http-logs').checked = saved.http_logs;
        loggingEdited = false;
        document.getElementById('logging-feedback').textContent = `Saved: ${saved.level} · HTTP logs ${saved.http_logs ? 'on' : 'off'}`;
      } catch (_) {
        loggingEdited = false;
        document.getElementById('logging-feedback').textContent = 'Save failed. Reloading current settings.';
      } finally {
        loggingSaving = false;
        controls.forEach(control => { control.disabled = false; });
        await refresh();
      }
    }
    for (const id of ['log-level', 'http-logs']) document.getElementById(id).addEventListener('change', saveLogging);
    const setState = (id, connection) => {
      const element = document.getElementById(id);
      element.textContent = connection.state;
      element.className = `value ${stateClass(connection.state)}`;
      const details = [];
      if (connection.version) details.push(['Version', connection.version]);
      if (connection.topic) details.push(['Topic', connection.topic]);
      details.push(['Last connected', timestamp(connection.last_connected_at)]);
      details.push(['State changed', timestamp(connection.last_state_change_at)]);
      details.push(['Last error', connection.last_error || 'None']);
      detailRows(`${id}-detail`, details);
    };
    const addCell = (row, value) => {
      const cell = document.createElement('td'); cell.textContent = value; row.append(cell); return cell;
    };
    const progressCell = worker => {
      const cell = document.createElement('td');
      if (worker.state !== 'processing') { cell.textContent = '—'; return cell; }
      const label = document.createElement('div');
      label.textContent = worker.progress_percent == null ? worker.phase : `${worker.progress_percent.toFixed(1)}%`;
      const bar = document.createElement('div');
      bar.className = `progress ${worker.progress_percent == null ? 'indeterminate' : ''}`;
      const fill = document.createElement('span');
      fill.style.width = worker.progress_percent == null ? '' : `${worker.progress_percent}%`;
      bar.append(fill); cell.append(label, bar); return cell;
    };
    function renderRetention(retention) {
      const parts = [['Retention', retention.enabled ? 'Enabled' : 'Disabled']];
      if (!retention.enabled) { detailRows('retention', parts); return; }
      parts.push(['Policy', `Older than ${retention.days} day(s)`]);
      parts.push(['Cleanup interval', `${retention.check_interval_hours} hour(s)`]);
      if (retention.last_run_at) {
        parts.push(['Last cleanup', timestamp(retention.last_run_at)]);
        parts.push(['Recordings removed', retention.deleted]);
        parts.push(['Space reclaimed', retention.reclaimed_size]);
        parts.push(['Cutoff', timestamp(retention.cutoff_at)]);
      } else parts.push(['Last cleanup', 'No cleanup run yet']);
      parts.push(['Next cleanup', timestamp(retention.next_run_at)]);
      detailRows('retention', parts);
    }
    async function refresh() {
      const revision = settingsRevision;
      try {
        const response = await fetch('/api/status');
        if (response.status === 401) { location.replace('/login'); return; }
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const status = await response.json();
        if (revision !== settingsRevision || loggingSaving) return;
        document.getElementById('subtitle').textContent = `v${status.exporter.version} · started ${timestamp(status.exporter.started_at)}`;
        setState('frigate', status.connections.frigate);
        setState('mqtt', status.connections.mqtt);
        const pending = status.queue.pending;
        document.getElementById('queue').textContent = pending === 0 ? 'Queue empty' : `${pending} review${pending === 1 ? '' : 's'} waiting`;
        document.getElementById('storage').textContent = status.storage ? `${status.storage.total_size} (${status.storage.file_count} files)` : 'Scanning…';
        renderRetention(status.retention);
        if (!loggingEdited && !loggingSaving) {
          document.getElementById('log-level').value = status.logging.level;
          document.getElementById('http-logs').checked = status.logging.http_logs;
        }

        const workers = document.getElementById('workers');
        workers.replaceChildren(...status.workers.map(worker => {
          const row = document.createElement('tr');
          const activity = worker.review_id ? `${worker.camera} · ${worker.review_id}` : 'Idle';
          const result = worker.last_error || worker.last_result || '—';
          addCell(row, worker.id); addCell(row, worker.state); addCell(row, activity);
          row.append(progressCell(worker));
          addCell(row, duration(worker.elapsed_seconds ?? worker.last_elapsed_seconds));
          addCell(row, timestamp(worker.started_at));
          addCell(row, timestamp(worker.last_completed_at));
          addCell(row, result); return row;
        }));

        const logs = document.getElementById('logs');
        logs.replaceChildren(...status.logs.slice().reverse().map(entry => {
          const row = document.createElement('tr');
          addCell(row, timestamp(entry.timestamp));
          const level = addCell(row, entry.level); level.className = `log-${entry.level.toLowerCase()}`;
          addCell(row, entry.logger); addCell(row, entry.message); return row;
        }));
        if (!status.logs.length) {
          const row = document.createElement('tr');
          const cell = addCell(row, 'No recent entries match this log level. Waiting for new events…');
          cell.colSpan = 4; logs.append(row);
        }
      } catch (_) {
        document.getElementById('subtitle').textContent = 'Dashboard connection failed. Retrying…';
      }
    }
    refresh(); setInterval(refresh, 2000);
  </script>
</body>
</html>"""
