# Changelog

All notable changes to this project will be documented in this file.

## [1.0.5] - 2026-10-01

### Added

- Recover Frigate connectivity automatically after a Frigate restart, even when no export is active.
- Show worker start and completion timestamps in the dashboard.
- Add configuration-loader validation with clearer YAML errors and regression coverage.

### Changed

- Remove redundant dashboard host and port fields from configuration examples; defaults remain `0.0.0.0:5050`.

## [1.0.4] - 2026-09-29

- Add live log-level controls and an explicit HTTP-log toggle (off by default).
- Organize connection and storage/retention details into labeled rows.

### Added

- Protect the dashboard and status API with credentials supplied through `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD`.
- Show worker phases, elapsed time, animated export progress, and byte-based local-copy progress.
- Show the 10 latest exporter log entries from a bounded, in-memory buffer with configured credentials redacted.
- Add a public health endpoint and Docker Compose health check.

### Improved

- Report connected, disconnected, and reconnecting states with connection timestamps and the latest error for Frigate and MQTT.
- Combine exported-storage usage with retention policy, cutoff, last cleanup, removed recordings, reclaimed space, and next cleanup.
- Publish dashboard port `5050` on the Docker host for private-network access.

## [1.0.3] - 2026-09-29

### Added

- Read-only dashboard with Frigate and MQTT connection status, queue depth, worker activity, retention results, and exported-recording storage usage.

### Improved

- Retention logs now include the total number and storage used by retained exported recordings.

## [1.0.2] - 2026-09-27

### Fixed

- Restored camera, label, and severity filtering for completed Frigate reviews.
- Added automatic MQTT reconnect and topic resubscription after temporary broker or network failures.
- Updated documentation and Docker Compose examples to match the current configuration format.
- Updated the lockfile and package metadata to version 1.0.2.

### Improved

- Validate malformed MQTT review payloads without stopping the message listener.
- Log active review filter configuration at startup.

## [1.0.1] - 2026-08-09

### Fixed

- Automatically recover from expired Frigate authentication sessions (401).
- Retry connecting to Frigate during startup until it becomes available.
- Gracefully handle Frigate startup delays without crashing.
- Automatically recover from temporary Frigate API failures.
- Prevent concurrent authentication attempts from multiple workers.

### Improved

- Cleaner startup and shutdown logging.
- Increased startup retry window for slow system boots.
- Improved overall resilience during Frigate restarts.

## [1.0.0] - 2026-07-27

### Added

- Initial release
- Export completed Frigate review events
- MQTT integration
- Docker support
- Retention cleanup
- Camera and label filtering
