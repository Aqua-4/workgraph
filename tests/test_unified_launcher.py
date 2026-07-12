from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest

from services.unified_launcher import _has_sync_config, _resolve_settings_path


class UnifiedLauncherTests(unittest.TestCase):
    def test_resolve_settings_prefers_my_settings_for_default_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            config_dir = temp / "config"
            config_dir.mkdir(parents=True, exist_ok=True)

            (config_dir / "my-settings.yaml").write_text(
                "sync_base_url: http://192.168.1.200:8000\nsync_token: token-1\n",
                encoding="utf-8",
            )

            original_cwd = Path.cwd()
            try:
                os.chdir(temp)
                resolved = _resolve_settings_path("config/settings.yaml")
            finally:
                os.chdir(original_cwd)

        self.assertEqual(resolved, Path("config/my-settings.yaml"))

    def test_has_sync_config_uses_my_settings_when_present(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            config_dir = temp / "config"
            config_dir.mkdir(parents=True, exist_ok=True)

            (config_dir / "settings.yaml").write_text(
                "sync_base_url: http://127.0.0.1:9000\nsync_token: \"\"\n",
                encoding="utf-8",
            )
            (config_dir / "my-settings.yaml").write_text(
                "sync_base_url: http://192.168.1.200:8000\nsync_token: token-1\n",
                encoding="utf-8",
            )

            original_cwd = Path.cwd()
            try:
                os.chdir(temp)
                has_config = _has_sync_config("config/settings.yaml")
            finally:
                os.chdir(original_cwd)

        self.assertTrue(has_config)

    def test_has_sync_config_false_when_token_missing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            config_path = temp / "config.yaml"
            config_path.write_text(
                "sync_base_url: http://192.168.1.200:8000\nsync_token: \"\"\n",
                encoding="utf-8",
            )

            has_config = _has_sync_config(str(config_path))

        self.assertFalse(has_config)


if __name__ == "__main__":
    unittest.main()
