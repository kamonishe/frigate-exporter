# Frigate Exporter

Frigate Exporter automatically exports completed Frigate review events to a local folder.

## Requirements

- Docker
- Docker Compose
- Frigate 0.18+
- MQTT enabled

## Docker Compose

Use the included `docker-compose.yml` as a starting point. The example is pinned to the current release.

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
