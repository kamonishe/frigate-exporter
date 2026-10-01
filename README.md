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

retention:
  enabled: true
  days: 30
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

### Dashboard

The log-level dropdown and **Enable HTTP logs (aiohttp)** checkbox apply automatically to dashboard and container logging. Recent entries are filtered immediately. HTTP logs are disabled by default. These temporary settings reset on restart to the configured logging level. Frigate, MQTT, and retention details are displayed in labeled rows.

The read-only dashboard listens on port `5050` by default. The supplied Compose file publishes `5050:5050`, making it available at `http://HOST_PRIVATE_IP:5050`. Sign in using the `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD` environment variables.

The dashboard shows detailed MQTT and Frigate connection state, queue depth, worker phases and export progress, recent exporter logs, and combined exported-storage and retention information. Its `/healthz` endpoint supports the supplied Docker health check. Set `dashboard.enabled: false` to disable the dashboard.

The dashboard has a login page and a Sign out button. Browser sessions expire after 12 hours or when the exporter restarts. HTTP Basic authentication remains available for API clients, without browser login popups. The application does not provide TLS. Keep port `5050` on a trusted private network or place the dashboard behind an HTTPS reverse proxy. Do not expose it directly to the public internet.

## Retention

When enabled, the retention worker removes exported `.mp4` files older than the configured number of days.

## Logging Levels

| Level | Description |
| ------- | ----------- |
| `ERROR` | Log only errors. |
| `INFO` | Log normal application events (recommended). |
| `DEBUG` | Log detailed information for troubleshooting. |

## License

MIT
