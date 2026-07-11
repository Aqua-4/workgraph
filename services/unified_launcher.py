"""Unified launcher to run collector and dashboard together."""

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def setup_logging() -> None:
    """Setup logging for the launcher."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(name)s: %(message)s",
    )


def run_unified(
    collector_config: str = "config/settings.yaml",
    dashboard_port: int = 4000,
    enable_sync: bool = True,
) -> None:
    """Run collector (self-healing) and dashboard together."""
    setup_logging()

    LOGGER.info("=" * 70)
    LOGGER.info("WorkGraph — Unified Collector + Dashboard")
    LOGGER.info("=" * 70)
    LOGGER.info(f"Collector config: {collector_config}")
    LOGGER.info(f"Dashboard port: http://127.0.0.1:{dashboard_port}")
    if enable_sync:
        LOGGER.info("Sync daemon: auto (enabled when sync config is present)")
    else:
        LOGGER.info("Sync daemon: disabled")
    LOGGER.info("Press Ctrl+C to stop everything")
    LOGGER.info("=" * 70)

    collector_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "services.collector_supervisor",
            "--config",
            collector_config,
        ]
    )

    time.sleep(1)

    dashboard_process = _start_dashboard(dashboard_port)
    sync_process: subprocess.Popen[None] | None = None
    if enable_sync and _has_sync_config(collector_config):
        sync_process = _start_sync_daemon(collector_config)
    elif enable_sync:
        LOGGER.warning(
            "sync_base_url/sync_token not found in %s. Sync daemon not started.",
            _resolve_settings_path(collector_config),
        )

    LOGGER.info(f"✓ Collector started (PID: {collector_process.pid})")
    LOGGER.info(f"✓ Dashboard started (PID: {dashboard_process.pid})")
    if sync_process is not None:
        LOGGER.info(f"✓ Sync daemon started (PID: {sync_process.pid})")
    LOGGER.info("")

    try:
        # Keep both processes running
        while True:
            # Check if either process has died
            collector_poll = collector_process.poll()
            dashboard_poll = dashboard_process.poll()

            if collector_poll is not None:
                LOGGER.error(
                    "Collector supervisor exited with code %s. Restarting in 2s.",
                    collector_poll,
                )
                time.sleep(2)
                collector_process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "services.collector_supervisor",
                        "--config",
                        collector_config,
                    ]
                )
                LOGGER.info(
                    "✓ Collector supervisor restarted (PID: %s)", collector_process.pid
                )

            if dashboard_poll is not None:
                LOGGER.warning(
                    "Dashboard exited with code %s. Restarting in 2s.",
                    dashboard_poll,
                )
                time.sleep(2)
                dashboard_process = _start_dashboard(dashboard_port)
                LOGGER.info("✓ Dashboard restarted (PID: %s)", dashboard_process.pid)

            if sync_process is not None:
                sync_poll = sync_process.poll()
                if sync_poll is not None:
                    LOGGER.warning(
                        "Sync daemon exited with code %s. Restarting in 2s.",
                        sync_poll,
                    )
                    time.sleep(2)
                    sync_process = _start_sync_daemon(collector_config)
                    LOGGER.info("✓ Sync daemon restarted (PID: %s)", sync_process.pid)

            time.sleep(1)

    except KeyboardInterrupt:
        LOGGER.info("\nShutting down...")
        collector_process.terminate()
        dashboard_process.terminate()
        if sync_process is not None:
            sync_process.terminate()

        try:
            collector_process.wait(timeout=5)
            dashboard_process.wait(timeout=5)
            if sync_process is not None:
                sync_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            LOGGER.warning("Force killing processes...")
            collector_process.kill()
            dashboard_process.kill()
            if sync_process is not None:
                sync_process.kill()
            collector_process.wait()
            dashboard_process.wait()
            if sync_process is not None:
                sync_process.wait()

        LOGGER.info("✓ Shutdown complete")


def _start_dashboard(port: int) -> subprocess.Popen[None]:
    return subprocess.Popen(
        [sys.executable, "-m", "main", "--web", "--port", str(port)]
    )


def _start_sync_daemon(config_path: str) -> subprocess.Popen[None]:
    return subprocess.Popen(
        [sys.executable, "-m", "main", "--config", config_path, "sync", "daemon"]
    )


def _resolve_settings_path(config_path: str) -> Path:
    requested = Path(config_path)
    default_path = Path("config/settings.yaml")
    personal_path = Path("config/my-settings.yaml")
    if requested == default_path and personal_path.exists():
        return personal_path
    return requested


def _has_sync_config(config_path: str) -> bool:
    settings_path = _resolve_settings_path(config_path)
    if not settings_path.exists():
        return False

    sync_base_url = ""
    sync_token = ""
    for raw_line in settings_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed_key = key.strip()
        parsed_value = value.strip().strip("\"'")
        if parsed_key == "sync_base_url":
            sync_base_url = parsed_value
        elif parsed_key == "sync_token":
            sync_token = parsed_value

    return bool(sync_base_url and sync_token)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run WorkGraph collector and dashboard together."
    )
    parser.add_argument(
        "--config",
        default="config/settings.yaml",
        help="Path to collector settings YAML file (default: config/settings.yaml)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=4000,
        help="Port for dashboard (default: 4000)",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Do not start sync daemon alongside collector and dashboard.",
    )
    args = parser.parse_args()

    run_unified(
        collector_config=args.config,
        dashboard_port=args.port,
        enable_sync=not args.no_sync,
    )
