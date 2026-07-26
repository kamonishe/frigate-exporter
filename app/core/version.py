from importlib.metadata import PackageNotFoundError, version

APP_NAME = "frigate-exporter"

try:
    APP_VERSION = version(APP_NAME)
except PackageNotFoundError:
    #
    # Development fallback when package metadata is unavailable.
    #
    APP_VERSION = "development"