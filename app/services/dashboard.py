from __future__ import annotations

from aiohttp import web

from app.models.config import DashboardConfig
from app.queue.review_queue import ReviewQueue
from app.services.runtime_status import RuntimeStatus


class DashboardServer:
    """Small read-only HTTP dashboard for exporter runtime status."""

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

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/api/status", self._status_response)

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

    async def _status_response(
        self,
        request: web.Request,
    ) -> web.Response:
        return web.json_response(
            self._status.snapshot(self._queue.size())
        )

    async def _index(self, request: web.Request) -> web.Response:
        return web.Response(
            text=_DASHBOARD_HTML,
            content_type="text/html",
        )


_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Frigate Exporter</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    body { background: #111827; color: #e5e7eb; margin: 0; }
    main { max-width: 1000px; margin: auto; padding: 2rem; }
    h1 { margin-bottom: .2rem; } .muted { color: #9ca3af; }
    .grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); }
    .card { background: #1f2937; border-radius: .75rem; padding: 1rem; }
    .value { font-size: 1.5rem; font-weight: 700; margin-top: .4rem; }
    .good { color: #34d399; } .bad { color: #f87171; }
    table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
    th, td { border-bottom: 1px solid #374151; padding: .65rem; text-align: left; }
  </style>
</head>
<body>
  <main>
    <h1>Frigate Exporter</h1>
    <p class="muted" id="subtitle">Loading status…</p>
    <section class="grid">
      <article class="card"><div>Frigate</div><div class="value" id="frigate">—</div></article>
      <article class="card"><div>MQTT</div><div class="value" id="mqtt">—</div></article>
      <article class="card"><div>Queued reviews</div><div class="value" id="queue">—</div></article>
      <article class="card"><div>Exported storage</div><div class="value" id="storage">—</div></article>
      <article class="card"><div>Retention</div><div class="value" id="retention">—</div></article>
    </section>
    <h2>Workers</h2>
    <table><thead><tr><th>Worker</th><th>Status</th><th>Activity</th><th>Last result</th></tr></thead>
    <tbody id="workers"></tbody></table>
  </main>
  <script>
    const setText = (id, text, good) => {
      const element = document.getElementById(id);
      element.textContent = text;
      element.className = `value ${good === undefined ? '' : good ? 'good' : 'bad'}`;
    };
    const timestamp = value => value ? new Date(value).toLocaleString() : '—';
    async function refresh() {
      try {
        const status = await fetch('/api/status').then(response => response.json());
        document.getElementById('subtitle').textContent = `v${status.exporter.version} · started ${timestamp(status.exporter.started_at)}`;
        setText('frigate', status.connections.frigate.connected ? 'Connected' : 'Unavailable', status.connections.frigate.connected);
        setText('mqtt', status.connections.mqtt.connected ? 'Connected' : 'Disconnected', status.connections.mqtt.connected);
        setText('queue', status.queue.pending);
        setText('storage', status.storage ? `${status.storage.total_size} (${status.storage.file_count} files)` : 'Scanning…');
        setText('retention', status.retention ? `${status.retention.deleted} removed` : 'No run yet');
        const workers = document.getElementById('workers');
        workers.replaceChildren(...status.workers.map(worker => {
          const row = document.createElement('tr');
          const activity = worker.review_id ? `${worker.camera} · ${worker.review_id}` : 'Idle';
          const result = worker.last_error || timestamp(worker.last_completed_at);
          [worker.id, worker.state, activity, result].forEach(value => {
            const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
          });
          return row;
        }));
      } catch (_) {
        document.getElementById('subtitle').textContent = 'Dashboard connection failed. Retrying…';
      }
    }
    refresh(); setInterval(refresh, 2000);
  </script>
</body>
</html>"""
