# Changelog

All notable changes to this project will be documented in this file.

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