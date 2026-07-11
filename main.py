from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sqlite3

from services.reporting import (
    default_goals_path,
    export_activity_sessions,
    goal_drift_markdown,
    generate_weekly_report_markdown,
    write_weekly_report,
)
from services.sync_daemon import SyncDaemon, SyncDaemonSettings
from services.sync_worker import HttpSyncClient, SyncWorker, SyncWorkerSettings

from db.repository import ActivityRepository
from services.activity_tagger import ActivityTagger
from services.collector_service import (
    CollectorService,
    CollectorSettings,
    configure_logging,
)
from workgraph.models import ActivitySession


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
    parser.add_argument(
        "--web",
        action="store_true",
        help="Start the web dashboard (requires FastAPI and Uvicorn).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the web dashboard (default: 8000).",
    )
    parser.add_argument(
        "--retag-existing",
        action="store_true",
        help="Recompute tags for all existing sessions using current config/tags.yaml rules.",
    )
    subparsers = parser.add_subparsers(dest="command")

    export_parser = subparsers.add_parser(
        "export", help="Export activity sessions as csv, json, or markdown."
    )
    export_parser.add_argument("format", choices=["csv", "json", "markdown"])
    export_parser.add_argument(
        "--output",
        help="Output file path. Defaults to exports/activity-export-<timestamp>.<ext>",
    )
    export_parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Only export sessions from the last N days.",
    )
    export_parser.add_argument(
        "--exclude-idle",
        action="store_true",
        help="Exclude idle sessions from export output.",
    )

    report_parser = subparsers.add_parser("report", help="Generate deterministic reports.")
    report_subparsers = report_parser.add_subparsers(dest="report_command")
    weekly_parser = report_subparsers.add_parser("weekly", help="Generate weekly report.")
    weekly_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Rolling window in days (default: 7).",
    )
    weekly_parser.add_argument(
        "--output",
        default=None,
        help="Optional output markdown file. Prints report to stdout when omitted.",
    )
    weekly_parser.add_argument(
        "--goals",
        default=str(default_goals_path()),
        help="Path to goals yaml used for drift analysis.",
    )

    goals_parser = subparsers.add_parser("goals", help="Analyze goal allocation drift.")
    goals_subparsers = goals_parser.add_subparsers(dest="goals_command")
    goals_analyze_parser = goals_subparsers.add_parser(
        "analyze", help="Analyze planned vs actual time allocation by goal."
    )
    goals_analyze_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Rolling window in days (default: 7).",
    )
    goals_analyze_parser.add_argument(
        "--goals",
        default=str(default_goals_path()),
        help="Path to goals yaml file.",
    )
    sync_parser = subparsers.add_parser("sync", help="Synchronize local data with central sync service.")
    sync_subparsers = sync_parser.add_subparsers(dest="sync_command")
    sync_once_parser = sync_subparsers.add_parser("once", help="Run one push/pull sync cycle.")
    sync_once_parser.add_argument(
        "--base-url",
        default=None,
        help="Sync service base URL (fallback: sync_base_url in config).",
    )
    sync_once_parser.add_argument(
        "--token",
        default=None,
        help="Device token for sync API (fallback: sync_token in config).",
    )
    sync_once_parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Push batch size (fallback: sync_batch_size in config or default 1000).",
    )
    sync_once_parser.add_argument(
        "--pull-limit",
        type=int,
        default=None,
        help="Pull page size (fallback: sync_pull_limit in config or default 1000).",
    )
    sync_once_parser.add_argument(
        "--max-pull-pages",
        type=int,
        default=None,
        help="Max pull pages per cycle (fallback: sync_max_pull_pages in config or default 20).",
    )
    sync_once_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="HTTP timeout for sync requests (fallback: sync_timeout_seconds in config or default 10).",
    )
    sync_daemon_parser = sync_subparsers.add_parser(
        "daemon", help="Run continuous sync loop with retry/backoff."
    )
    sync_daemon_parser.add_argument(
        "--base-url",
        default=None,
        help="Sync service base URL (fallback: sync_base_url in config).",
    )
    sync_daemon_parser.add_argument(
        "--token",
        default=None,
        help="Device token for sync API (fallback: sync_token in config).",
    )
    sync_daemon_parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Push batch size (fallback: sync_batch_size in config or default 1000).",
    )
    sync_daemon_parser.add_argument(
        "--pull-limit",
        type=int,
        default=None,
        help="Pull page size (fallback: sync_pull_limit in config or default 1000).",
    )
    sync_daemon_parser.add_argument(
        "--max-pull-pages",
        type=int,
        default=None,
        help="Max pull pages per cycle (fallback: sync_max_pull_pages in config or default 20).",
    )
    sync_daemon_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="HTTP timeout for sync requests (fallback: sync_timeout_seconds in config or default 10).",
    )
    sync_daemon_parser.add_argument(
        "--interval-seconds",
        type=float,
        default=None,
        help="Seconds between successful sync cycles (fallback: sync_interval_seconds in config or default 60).",
    )
    sync_daemon_parser.add_argument(
        "--backoff-base-seconds",
        type=float,
        default=None,
        help="Initial retry backoff seconds (fallback: sync_backoff_base_seconds in config or default 1).",
    )
    sync_daemon_parser.add_argument(
        "--backoff-max-seconds",
        type=float,
        default=None,
        help="Maximum retry backoff seconds (fallback: sync_backoff_max_seconds in config or default 60).",
    )
    sync_subparsers.add_parser(
        "migrate",
        help="Run local sync schema migration/backfill on the configured SQLite database.",
    )
    args = parser.parse_args()

    if args.command == "export":
        run_export_command(args)
    elif args.command == "report" and args.report_command == "weekly":
        run_weekly_report_command(args)
    elif args.command == "goals" and args.goals_command == "analyze":
        run_goals_analyze_command(args)
    elif args.command == "sync" and args.sync_command == "once":
        run_sync_once_command(args)
    elif args.command == "sync" and args.sync_command == "daemon":
        run_sync_daemon_command(args)
    elif args.command == "sync" and args.sync_command == "migrate":
        run_sync_migrate_command(args)
    elif args.retag_existing:
        retag_existing_sessions(args.config)
    elif args.web:
        start_web_dashboard(args.port)
    else:
        settings = load_settings(args.config)
        configure_logging(settings.log_path)

        service = CollectorService(settings)
        if args.once:
            service.collect_once()
            service.flush()
            service.repository.close()
        else:
            service.run_forever()


def run_export_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    output_path = export_activity_sessions(
        db_path=settings.database_path,
        export_format=args.format,
        output_path=args.output,
        days=args.days,
        include_idle=not args.exclude_idle,
    )
    print(f"Export complete: {output_path}")


def run_weekly_report_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    goals_path = Path(args.goals)
    goals_arg = str(goals_path) if goals_path.exists() else None

    if args.output:
        output_path = write_weekly_report(
            db_path=settings.database_path,
            output_path=args.output,
            days=args.days,
            goals_path=goals_arg,
        )
        print(f"Weekly report generated: {output_path}")
        return

    report = generate_weekly_report_markdown(
        db_path=settings.database_path,
        days=args.days,
        goals_path=goals_arg,
    )
    print(report)


def run_goals_analyze_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    goals_path = Path(args.goals)
    if not goals_path.exists():
        print(f"Goals file not found: {goals_path}")
        print("Create config/my-goals.yaml or config/goals.yaml with a top-level 'goals' mapping.")
        return

    report = goal_drift_markdown(
        db_path=settings.database_path,
        goals_path=str(goals_path),
        days=args.days,
    )
    print(report)


def run_sync_once_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    raw_values = _read_simple_yaml(Path(args.config)) if Path(args.config).exists() else {}

    base_url = args.base_url or raw_values.get("sync_base_url")
    token = args.token or raw_values.get("sync_token")
    if not base_url:
        print("sync_base_url missing. Pass --base-url or set sync_base_url in config.")
        return
    if not token:
        print("sync_token missing. Pass --token or set sync_token in config.")
        return

    batch_size = int(args.batch_size or raw_values.get("sync_batch_size", 1000))
    pull_limit = int(args.pull_limit or raw_values.get("sync_pull_limit", 1000))
    max_pull_pages = int(args.max_pull_pages or raw_values.get("sync_max_pull_pages", 20))
    timeout_seconds = float(args.timeout_seconds or raw_values.get("sync_timeout_seconds", 10.0))

    worker_settings = SyncWorkerSettings(
        batch_size=batch_size,
        pull_limit=pull_limit,
        max_pull_pages=max_pull_pages,
    )
    client = HttpSyncClient(base_url=str(base_url), token=str(token), timeout_seconds=timeout_seconds)

    with ActivityRepository(settings.database_path, identity_path=settings.identity_path) as repository:
        worker = SyncWorker(repository, client, worker_settings)
        summary = worker.run_once()

    print("Sync complete")
    print(f"Push: {summary['push']}")
    print(f"Pull: {summary['pull']}")


def run_sync_daemon_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    raw_values = _read_simple_yaml(Path(args.config)) if Path(args.config).exists() else {}

    base_url = args.base_url or raw_values.get("sync_base_url")
    token = args.token or raw_values.get("sync_token")
    if not base_url:
        print("sync_base_url missing. Pass --base-url or set sync_base_url in config.")
        return
    if not token:
        print("sync_token missing. Pass --token or set sync_token in config.")
        return

    worker_settings = SyncWorkerSettings(
        batch_size=int(args.batch_size or raw_values.get("sync_batch_size", 1000)),
        pull_limit=int(args.pull_limit or raw_values.get("sync_pull_limit", 1000)),
        max_pull_pages=int(args.max_pull_pages or raw_values.get("sync_max_pull_pages", 20)),
    )
    daemon_settings = SyncDaemonSettings(
        interval_seconds=float(args.interval_seconds or raw_values.get("sync_interval_seconds", 60.0)),
        backoff_base_seconds=float(
            args.backoff_base_seconds or raw_values.get("sync_backoff_base_seconds", 1.0)
        ),
        backoff_max_seconds=float(
            args.backoff_max_seconds or raw_values.get("sync_backoff_max_seconds", 60.0)
        ),
    )
    timeout_seconds = float(args.timeout_seconds or raw_values.get("sync_timeout_seconds", 10.0))

    client = HttpSyncClient(base_url=str(base_url), token=str(token), timeout_seconds=timeout_seconds)

    print("Starting sync daemon. Press Ctrl+C to stop.")
    with ActivityRepository(settings.database_path, identity_path=settings.identity_path) as repository:
        worker = SyncWorker(repository, client, worker_settings)
        daemon = SyncDaemon(worker, daemon_settings)
        daemon.run_forever()


def run_sync_migrate_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    db_path = Path(settings.database_path)

    before = _count_sync_metadata_gaps(db_path)
    with ActivityRepository(settings.database_path, identity_path=settings.identity_path):
        pass
    after = _count_sync_metadata_gaps(db_path)

    print("Sync migration complete")
    print(f"Database: {db_path}")
    print(f"Identity: {settings.identity_path}")
    print(f"Sessions with missing sync metadata: {before['activity_sessions']} -> {after['activity_sessions']}")
    print(f"Journal entries with missing sync metadata: {before['journal_entries']} -> {after['journal_entries']}")
    print(f"Reflections with missing sync metadata: {before['daily_reflections']} -> {after['daily_reflections']}")


def retag_existing_sessions(config_path: str) -> None:
    """Retag all existing sessions in DB with current tagging rules."""
    settings = load_settings(config_path)
    configure_logging(settings.log_path)

    repository = ActivityRepository(settings.database_path)
    try:
        backup_path = repository.backup_database()
        print(f"Backup created: {backup_path}")

        tagger = ActivityTagger()
        rows = repository.all_sessions()
        total = len(rows)
        updated = 0

        for row in rows:
            session = _session_from_row(row)
            new_tag = tagger.tag_session(session)
            old_tag = row["tag"]
            if new_tag != old_tag:
                repository.update_session_tag(int(row["id"]), new_tag)
                updated += 1

        print(f"Retag complete: updated {updated} of {total} sessions")
    finally:
        repository.close()


def _session_from_row(row: dict) -> ActivitySession:
    return ActivitySession(
        start_time=_parse_datetime(str(row["start_time"])),
        end_time=_parse_datetime(str(row["end_time"])),
        duration_sec=int(row["duration_sec"]),
        app_name=str(row["app_name"]),
        process_name=row["process_name"],
        window_title=row["window_title"],
        browser_domain=row["browser_domain"],
        is_idle=bool(row["is_idle"]),
        idle_seconds=int(row["idle_seconds"]),
        platform=str(row["platform"]),
        git_repo=row["git_repo"],
        git_branch=row["git_branch"],
        context_switches=int(row["context_switches"]),
        tag=row["tag"],
    )


def _parse_datetime(value: str) -> datetime:
    # sqlite rows may contain ISO timestamps with/without timezone.
    return datetime.fromisoformat(value)


def start_web_dashboard(port: int = 8000) -> None:
    """Start the web dashboard server."""
    try:
        import uvicorn
        from api.app import app
    except ImportError:
        print("Error: FastAPI and Uvicorn required for web dashboard.")
        print("Install with: uv sync")
        return

    print(f"Starting WorkGraph Dashboard on http://127.0.0.1:{port}")
    print("Press Ctrl+C to stop the server")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


def load_settings(path: str) -> CollectorSettings:
    values = {
        "database_path": "activity.db",
        "poll_interval_seconds": 5.0,
        "idle_threshold_seconds": 300,
        "session_gap_seconds": 90,
        "browser_history_lookback_seconds": 600,
        "log_path": "logs/workgraph.log",
        "identity_path": "config/identity.json",
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
        identity_path=str(values["identity_path"]),
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


def _count_sync_metadata_gaps(db_path: Path) -> dict[str, int]:
    if not db_path.exists():
        return {
            "activity_sessions": 0,
            "journal_entries": 0,
            "daily_reflections": 0,
        }

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        counts = {}
        counts["activity_sessions"] = _count_table_sync_gaps(conn, "activity_sessions")
        counts["journal_entries"] = _count_table_sync_gaps(conn, "journal_entries")
        counts["daily_reflections"] = _count_table_sync_gaps(conn, "daily_reflections")
        return counts
    finally:
        conn.close()


def _count_table_sync_gaps(conn: sqlite3.Connection, table_name: str) -> int:
    if not _table_exists(conn, table_name):
        return 0

    columns = _table_columns(conn, table_name)
    if not columns:
        return 0

    required = ["uuid", "user_id", "device_id", "updated_at"]
    if not all(column in columns for column in required):
        return _safe_count_rows(conn, table_name)

    row = conn.execute(
        f"""
        SELECT COUNT(*) AS missing_count
        FROM {table_name}
        WHERE uuid IS NULL
           OR user_id IS NULL
           OR device_id IS NULL
           OR updated_at IS NULL
        """
    ).fetchone()
    return int(row["missing_count"] if row is not None else 0)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _safe_count_rows(conn: sqlite3.Connection, table_name: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    if row is None:
        return 0
    return int(row[0])


if __name__ == "__main__":
    main()
