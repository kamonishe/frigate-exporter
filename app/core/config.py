from pathlib import Path

import yaml

from app.models.config import Config

#
# Configuration search order:
#
# 1. /config/config.yml       (Docker)
# 2. ./config/config.yml      (Local development)
#
CONFIG_LOCATIONS = [
    Path("/config/config.yml"),
    Path("config/config.yml"),
]


def load_config() -> Config:
    config_file = next(
        (path for path in CONFIG_LOCATIONS if path.exists()),
        None,
    )

    if config_file is None:
        searched = "\n".join(
            f" - {path}"
            for path in CONFIG_LOCATIONS
        )

        raise FileNotFoundError(
            "Configuration file not found.\n"
            f"Searched:\n{searched}"
        )

    with config_file.open(
        "r",
        encoding="utf-8",
    ) as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise RuntimeError(
            f"Configuration file '{config_file}' is empty."
        )

    return Config.model_validate(raw)
