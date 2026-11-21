import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from services.collector_supervisor import CollectorSupervisor
from services.unified_launcher import (
    _has_sync_config,
    _resolve_settings_path,
    run_unified,
)


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
                'sync_base_url: http://127.0.0.1:9000\nsync_token: ""\n',
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
                'sync_base_url: http://192.168.1.200:8000\nsync_token: ""\n',
                encoding="utf-8",
            )

            has_config = _has_sync_config(str(config_path))

        self.assertFalse(has_config)

    def test_collector_failure_signals_error(self) -> None:
        supervisor = CollectorSupervisor()
        process = Mock(pid=123)
        process.wait.return_value = 1
        with (
            patch.object(supervisor, "_configure_logging"),
            patch.object(supervisor, "_register_signal_handlers"),
            patch(
                "services.collector_supervisor.subprocess.Popen", return_value=process
            ),
            patch("services.collector_supervisor.error_beep") as beep,
            patch(
                "services.collector_supervisor.time.sleep",
                side_effect=lambda _: setattr(supervisor, "_stopping", True),
            ),
        ):
            supervisor.run_forever()
        beep.assert_called_once_with()

    def test_dashboard_start_failure_signals_error_and_stops_collector(self) -> None:
        collector = Mock(pid=123)
        with (
            patch("services.unified_launcher.subprocess.Popen", return_value=collector),
            patch("services.unified_launcher._start_dashboard", side_effect=OSError),
            patch("services.unified_launcher.time.sleep"),
            patch("services.unified_launcher.error_beep") as beep,
        ):
            with self.assertRaises(OSError):
                run_unified(enable_sync=False)
        beep.assert_called_once_with()
        collector.terminate.assert_called_once_with()

    def test_dashboard_exit_signals_error(self) -> None:
        collector = Mock(pid=123)
        collector.poll.return_value = None
        dashboard = Mock(pid=124)
        dashboard.poll.return_value = 1
        restarted = Mock(pid=125)
        with (
            patch("services.unified_launcher.subprocess.Popen", return_value=collector),
            patch(
                "services.unified_launcher._start_dashboard",
                side_effect=[dashboard, restarted],
            ),
            patch(
                "services.unified_launcher.time.sleep",
                side_effect=[None, None, KeyboardInterrupt],
            ),
            patch("services.unified_launcher.error_beep") as beep,
        ):
            run_unified(enable_sync=False)
        beep.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
