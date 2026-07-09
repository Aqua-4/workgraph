from __future__ import annotations

import argparse
from pathlib import Path

from services.collector_service import (
    CollectorService,
    CollectorSettings,
    configure_logging,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the WorkGraph v1 collector.")
    parser.add_argument(
        "--config",
        default="config/settings.yaml",
        help="Path to a simple YAML settings file.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Collect one sample and exit after flushing it to SQLite.",
    )
    args = parser.parse_args()

    settings = load_settings(args.config)
    configure_logging(settings.log_path)

    service = CollectorService(settings)
    if args.once:
        service.collect_once()
        service.flush()
        service.repository.close()
    else:
        service.run_forever()


def load_settings(path: str) -> CollectorSettings:
    values = {
        "database_path": "activity.db",
        "poll_interval_seconds": 5.0,
        "idle_threshold_seconds": 300,
        "session_gap_seconds": 90,
        "browser_history_lookback_seconds": 600,
        "log_path": "logs/workgraph.log",
    }
    config_path = Path(path)
    if config_path.exists():
        values.update(_read_simple_yaml(config_path))
    return CollectorSettings(
        database_path=str(values["database_path"]),
        poll_interval_seconds=float(values["poll_interval_seconds"]),
        idle_threshold_seconds=int(values["idle_threshold_seconds"]),
        session_gap_seconds=int(values["session_gap_seconds"]),
        browser_history_lookback_seconds=int(values["browser_history_lookback_seconds"]),
        log_path=str(values["log_path"]),
    )


def _read_simple_yaml(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip()] = value.strip().strip("\"'")
    return parsed


if __name__ == "__main__":
    main()
