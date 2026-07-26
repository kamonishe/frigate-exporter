import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    """
    Configure application logging.

    Supported levels:
        ERROR
        WARNING
        INFO
        DEBUG
    """

    level = level.upper()

    numeric_level = getattr(
        logging,
        level,
        logging.INFO,
    )

    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )

    logger = logging.getLogger(__name__)

    logger.debug(
        "Logging initialized (level=%s)",
        logging.getLevelName(numeric_level),
    )
