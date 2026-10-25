# Frigate Exporter

<p align="center">
  <img src="app/static/clip-courier-badge.svg" alt="Clip Courier — Frigate Exporter logo" width="180" height="180">
</p>

Frigate Exporter automatically exports completed Frigate review events to a local folder.

## Requirements

- Docker
- Docker Compose
- Frigate 0.18+
- MQTT enabled

## Docker Compose

Use the included `docker-compose.yml` as a starting point. The example is pinned to the current release. Copy `.env.example` to `.env`, replace the dashboard credentials, and then start the container:

```bash
cp .env.example .env
docker compose up -d
```

The `.env` file is ignored by Git. You can alternatively provide `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD` through the shell or your container-management interface.

## Configuration

Copy `config/config.example.yml` to `config/config.yml` and set your Frigate and MQTT credentials.

```yaml
frigate:
  url: "https://frigate.local:8971"
  username: "YOUR_USERNAME"
  password: "YOUR_PASSWORD"
  verify_ssl: false
  timeout: 30

mqtt:
  host: "mqtt.local"
  port: 1883
  username: "YOUR_MQTT_USERNAME"
  password: "YOUR_MQTT_PASSWORD"
  topic: "frigate/reviews"

export:
  output: "/exports"
  workers: 3
  pre_capture: 3
  post_capture: 3
  export_timeout: 900
  max_attempts: 5

retention:
  enabled: true
  # Set either days or hours, but not both.
  days: 30
  # hours: 1
  check_interval_hours: 24

filters:
  cameras: []
  labels: []
  severity:
    - alert

dashboard:
  enabled: true

logging:
  level: INFO
```

### Filters

All filter lists are optional.

- `cameras`: export reviews only from the listed cameras. An empty list allows all cameras.
- `labels`: export reviews when at least one configured label is present in the review's detected objects or audio labels. An empty list allows all labels.
- `severity`: export only the listed review severities. An empty list allows all severities. Frigate review severities are `alert` and `detection`.

Frigate publishes review events on the `frigate/reviews` topic. The exporter processes only the final `end` message for each review.

The exporter automatically reconnects to MQTT after temporary connection failures.

Each Frigate export is bounded by export_timeout seconds (900 by default). If
Frigate leaves an export stuck or removes it before completion, the exporter
marks the review for retry instead of holding a worker indefinitely. Failed
exports are attempted at most max_attempts times (5 by default). When retention
is enabled, a failed review whose event has moved outside the retention window
is marked unavailable rather than retried indefinitely. Unavailable review
records are removed by the normal retention cleanup after they leave that
window.

### Dashboard

The log-level dropdown and **Enable HTTP logs (aiohttp)** checkbox apply automatically to dashboard and container logging. Recent entries are filtered immediately. HTTP logs are disabled by default. These temporary settings reset on restart to the configured logging level. Frigate, MQTT, and retention details are displayed in labeled rows.

The authenticated dashboard listens on port `5050` by default. The supplied Compose file publishes `5050:5050`, making it available at `http://HOST_PRIVATE_IP:5050`. Sign in using the `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD` environment variables.

The dashboard shows detailed MQTT and Frigate connection state, durable queue depth with the oldest waiting review, worker phases and export progress, per-camera export and failure summaries, recent exporter logs, and combined exported-storage and retention information. Reconciliation reports `Processing` while a recovery scan is active and keeps live progress separate from the last completed run. Its `/healthz` endpoint supports the supplied Docker health check. Set `dashboard.enabled: false` to disable the dashboard.

The dashboard has a login page, a **Restart exporter** button, and a Sign out button. Restart requires confirmation, sends a graceful shutdown signal, and relies on the container restart policy to bring the exporter back. Browser sessions expire after 12 hours or when the exporter restarts. HTTP Basic authentication remains available for API clients, without browser login popups. The application does not provide TLS. Keep port `5050` on a trusted private network or place the dashboard behind an HTTPS reverse proxy. Do not expose it directly to the public internet.

## Retention

When enabled, the retention worker removes exported `.mp4` files older than the configured retention window. Retention uses the Frigate review event end time for newly exported files, so backlog processing does not extend their lifetime. Use `days` for day-based retention or replace it with `hours` for a sub-day window such as `hours: 1`; set only one of these fields. The same window controls when unavailable review records are purged and when retry attempts expire.

### Review reconciliation

Reconciliation is enabled by default. Every `interval_minutes`, the exporter asks Frigate for completed reviews from the previous `lookback_days` (paging through large result sets) and queues any review not already recorded as completed. On the first run with an existing export directory, it establishes a baseline so historical recordings are not re-exported. Review state is stored in `.frigate-exporter-reviews.sqlite3` beneath `export.output`, so MQTT duplicates and exporter restarts do not create duplicate work. Set `reconciliation.enabled: false` to disable the recovery pass.


## Logging Levels

| Level | Description |
| ------- | ----------- |
| `ERROR` | Log only errors. |
| `INFO` | Log normal application events (recommended). |
| `DEBUG` | Log detailed information for troubleshooting. |

## License

MIT
