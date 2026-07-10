"""Unified launcher to run collector and dashboard together."""

import argparse
import logging
import subprocess
import sys
import time

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
) -> None:
    """Run collector (self-healing) and dashboard together."""
    setup_logging()

    LOGGER.info("=" * 70)
    LOGGER.info("WorkGraph — Unified Collector + Dashboard")
    LOGGER.info("=" * 70)
    LOGGER.info(f"Collector config: {collector_config}")
    LOGGER.info(f"Dashboard port: http://127.0.0.1:{dashboard_port}")
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

    LOGGER.info(f"✓ Collector started (PID: {collector_process.pid})")
    LOGGER.info(f"✓ Dashboard started (PID: {dashboard_process.pid})")
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
                LOGGER.info("✓ Collector supervisor restarted (PID: %s)", collector_process.pid)

            if dashboard_poll is not None:
                LOGGER.warning(
                    "Dashboard exited with code %s. Restarting in 2s.",
                    dashboard_poll,
                )
                time.sleep(2)
                dashboard_process = _start_dashboard(dashboard_port)
                LOGGER.info("✓ Dashboard restarted (PID: %s)", dashboard_process.pid)

            time.sleep(1)

    except KeyboardInterrupt:
        LOGGER.info("\nShutting down...")
        collector_process.terminate()
        dashboard_process.terminate()

        try:
            collector_process.wait(timeout=5)
            dashboard_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            LOGGER.warning("Force killing processes...")
            collector_process.kill()
            dashboard_process.kill()
            collector_process.wait()
            dashboard_process.wait()

        LOGGER.info("✓ Shutdown complete")


def _start_dashboard(port: int) -> subprocess.Popen[None]:
    return subprocess.Popen(
        [sys.executable, "-m", "main", "--web", "--port", str(port)]
    )


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
    args = parser.parse_args()

    run_unified(collector_config=args.config, dashboard_port=args.port)
