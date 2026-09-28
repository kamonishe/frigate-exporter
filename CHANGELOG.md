# Changelog

All notable changes to this project will be documented in this file.

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
