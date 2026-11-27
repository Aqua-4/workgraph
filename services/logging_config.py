"""Shared logging configuration for WorkGraph processes."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def parse_log_level(value: str) -> int:
    """Convert a configured level name to a logging level."""
    normalized = value.strip().upper()
    level = getattr(logging, normalized, None)
    if isinstance(level, int):
        return level
    raise ValueError(
        f"Invalid log_level {value!r}; expected DEBUG, INFO, WARNING, "
        "ERROR, or CRITICAL"
    )


def configure_logging(
    log_path: str = "logs/workgraph.log",
    log_level: str = "INFO",
    *,
    console: bool = True,
) -> None:
    """Configure the process-wide logger with console and rotating file output."""
    level = parse_log_level(log_level)
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(DEFAULT_LOG_FORMAT)
    file_handler = RotatingFileHandler(
        path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    handlers: list[logging.Handler] = [file_handler]
    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        handlers.append(console_handler)

    logging.basicConfig(
        level=level,
        handlers=handlers,
        force=True,
    )
