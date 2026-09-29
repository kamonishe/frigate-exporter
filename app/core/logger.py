import logging
import sys
from collections.abc import Callable

LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_http_logs = False


class HTTPLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return _http_logs or not (
            record.name == "aiohttp" or record.name.startswith("aiohttp.")
        )


def logging_settings() -> dict[str, object]:
    return {"level": logging.getLevelName(logging.getLogger().level), "http_logs": _http_logs}


def change_logging(level: str, http_logs: bool) -> None:
    global _http_logs
    if level not in LOG_LEVELS or type(http_logs) is not bool:
        raise ValueError("Invalid logging settings")
    logging.getLogger().setLevel(level)
    _http_logs = http_logs


class RecentLogHandler(logging.Handler):
    """Forwards formatted log records to the dashboard's bounded buffer."""

    def __init__(
        self,
        callback: Callable[[str, str, str], None],
    ) -> None:
        super().__init__()
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._callback(
                record.levelname,
                record.name,
                record.getMessage(),
            )
        except Exception:  # noqa: BLE001 - logging must not break the app
            self.handleError(record)


def setup_logging(
    level: str = "INFO",
    recent_log_callback: Callable[[str, str, str], None] | None = None,
) -> None:
    """Configure stdout logging and optional bounded dashboard logging."""

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    handlers: list[logging.Handler] = [stdout_handler]

    if recent_log_callback is not None:
        handlers.append(RecentLogHandler(recent_log_callback))

    for handler in handlers:
        handler.addFilter(HTTPLogFilter())
    change_logging(logging.getLevelName(numeric_level), False)

    logging.basicConfig(
        level=numeric_level,
        handlers=handlers,
        force=True,
    )

    logging.getLogger(__name__).debug(
        "Logging initialized (level=%s)",
        logging.getLevelName(numeric_level),
    )
