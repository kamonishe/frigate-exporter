import os
from pathlib import Path
from typing import Any

import yaml

from app.models.config import Config

# Configuration search order: Docker path first, then local development path.
CONFIG_LOCATIONS = (
    Path("/config/config.yml"),
    Path("config/config.yml"),
)


def find_config_file() -> Path:
    """Return the first existing configuration file."""
    config_file = next((path for path in CONFIG_LOCATIONS if path.exists()), None)
    if config_file is None:
        searched = "\n".join(f" - {path}" for path in CONFIG_LOCATIONS)
        raise FileNotFoundError(
            "Configuration file not found.\n"
            f"Searched:\n{searched}"
        )
    return config_file


def read_config_file(config_file: Path) -> dict[str, Any]:
    """Read and validate the YAML document shape before model validation."""
    try:
        with config_file.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        problem = getattr(exc, "problem", str(exc))
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise RuntimeError(
            f"Invalid YAML in configuration file '{config_file}'{location}: {problem}"
        ) from exc

    if raw is None:
        raise RuntimeError(f"Configuration file '{config_file}' is empty.")
    if not isinstance(raw, dict):
        raise TypeError(
            f"Configuration file '{config_file}' must contain a YAML mapping at the root."
        )
    return raw


def apply_environment_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply supported environment overrides without exposing secret values."""
    document = dict(raw)
    dashboard = document.get("dashboard")
    if dashboard is None:
        dashboard = {}
    elif not isinstance(dashboard, dict):
        raise TypeError("The 'dashboard' configuration must be a YAML mapping.")
    else:
        dashboard = dict(dashboard)

    for field, environment_name in (
        ("username", "DASHBOARD_USERNAME"),
        ("password", "DASHBOARD_PASSWORD"),
    ):
        value = os.getenv(environment_name)
        if value is not None:
            dashboard[field] = value

    document["dashboard"] = dashboard
    return document


def load_config() -> Config:
    config_file = find_config_file()
    raw = read_config_file(config_file)
    return Config.model_validate(apply_environment_overrides(raw))
