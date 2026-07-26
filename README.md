# Frigate Exporter

Frigate Exporter automatically exports completed Frigate review events to a local folder.

## Requirements

- Docker
- Docker Compose
- Frigate 0.18+
- MQTT enabled

## Docker Compose

Use the included `docker-compose.yml` as a starting point.

## Base Configuration

```yaml
frigate:
  url: "https://frigate.local:8971"
  username: ""
  password: ""
  verify_ssl: false

mqtt:
  server: "mqtt.local"
  port: 1883
  username: ""
  password: ""

export:
  output_dir: "/exports"
  pre_capture: 5
  post_capture: 5

logging:
  level: INFO
```

For all available configuration options, see:

```text
config/config.example.yml
```

## Logging Levels

| Level | Description |
| ------- | ----------- |
| `ERROR` | Log only errors. |
| `INFO` | Log normal application events (recommended). |
| `DEBUG` | Log detailed information for troubleshooting. |

## License

MIT
