import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import config as config_module


class ConfigTests(unittest.TestCase):
    def test_dashboard_credentials_are_loaded_from_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_file = Path(temporary) / "config.yml"
            config_file.write_text(
                """
frigate:
  url: https://frigate:8971
  username: frigate
  password: secret
mqtt:
  host: mqtt
  port: 1883
  username: mqtt
  password: secret
  topic: frigate/reviews
export:
  output: /exports
  workers: 1
  pre_capture: 1
  post_capture: 1
retention: {}
filters:
  cameras: []
  labels: []
  severity: []
logging:
  level: INFO
""",
                encoding="utf-8",
            )

            with (
                patch.object(config_module, "CONFIG_LOCATIONS", [config_file]),
                patch.dict(
                    os.environ,
                    {
                        "DASHBOARD_USERNAME": "viewer",
                        "DASHBOARD_PASSWORD": "dashboard-secret",
                    },
                ),
            ):
                config = config_module.load_config()

            self.assertEqual(config.dashboard.username, "viewer")
            self.assertEqual(config.dashboard.password, "dashboard-secret")

    def test_invalid_yaml_reports_file_and_location(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_file = Path(temporary) / "config.yml"
            config_file.write_text("frigate: [broken\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, r"Invalid YAML.*config.yml.*line"):
                config_module.read_config_file(config_file)

    def test_non_mapping_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_file = Path(temporary) / "config.yml"
            config_file.write_text("- not-a-config\n", encoding="utf-8")
            with self.assertRaisesRegex(TypeError, "must contain a YAML mapping"):
                config_module.read_config_file(config_file)

    def test_dashboard_mapping_is_required(self) -> None:
        with self.assertRaisesRegex(TypeError, "dashboard.*mapping"):
            config_module.apply_environment_overrides({"dashboard": "invalid"})

    def test_environment_overrides_are_applied(self) -> None:
        raw = {"dashboard": {"enabled": True}}
        with patch.dict(os.environ, {"DASHBOARD_USERNAME": "viewer", "DASHBOARD_PASSWORD": "secret"}):
            result = config_module.apply_environment_overrides(raw)
        self.assertEqual(result["dashboard"]["username"], "viewer")
        self.assertEqual(result["dashboard"]["password"], "secret")
        self.assertEqual(raw["dashboard"], {"enabled": True})


if __name__ == "__main__":
    unittest.main()
