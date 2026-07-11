from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from main import load_settings


class MainSettingsTests(unittest.TestCase):
    def test_load_settings_reads_identity_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "settings.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "database_path: temp.db",
                        "poll_interval_seconds: 3",
                        "idle_threshold_seconds: 120",
                        "session_gap_seconds: 45",
                        "browser_history_lookback_seconds: 300",
                        "log_path: logs/test.log",
                        "identity_path: config/custom-identity.json",
                    ]
                ),
                encoding="utf-8",
            )

            settings = load_settings(str(config_path))

        self.assertEqual(settings.database_path, "temp.db")
        self.assertEqual(settings.identity_path, "config/custom-identity.json")


if __name__ == "__main__":
    unittest.main()
