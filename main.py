from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from db.repository import ActivityRepository
from services.activity_tagger import ActivityTagger
from services.collector_service import (
    CollectorService,
    CollectorSettings,
    configure_logging,
)
from services.reporting import (
    default_goals_path,
    export_activity_sessions,
    generate_goals_report_markdown,
    generate_monthly_report_markdown,
    generate_sync_report_markdown,
    generate_weekly_report_markdown,
    goal_drift_markdown,
    write_weekly_report,
)
from services.sync_daemon import SyncDaemon, SyncDaemonSettings
from services.sync_worker import HttpSyncClient, SyncWorker, SyncWorkerSettings
from workgraph.models import ActivitySession

DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
PERSONAL_SETTINGS_PATH = Path("config/my-settings.yaml")
DEFAULT_IDENTITY_PATH = Path("config/identity.json")
PERSONAL_IDENTITY_PATH = Path("config/my-identity.json")


def _default_config_path_str() -> str:
    return str(
        PERSONAL_SETTINGS_PATH
        if PERSONAL_SETTINGS_PATH.exists()
        else DEFAULT_SETTINGS_PATH
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the WorkGraph v1 collector.")
    parser.add_argument(
        "--config",
        default=_default_config_path_str(),
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

    report_parser = subparsers.add_parser(
        "report", help="Generate deterministic reports."
    )
    report_subparsers = report_parser.add_subparsers(dest="report_command")
    weekly_parser = report_subparsers.add_parser(
        "weekly", help="Generate weekly report."
    )
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

    monthly_parser = report_subparsers.add_parser(
        "monthly", help="Generate monthly report."
    )
    monthly_parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Rolling window in days (default: 30).",
    )
    monthly_parser.add_argument(
        "--output",
        default=None,
        help="Optional output markdown file. Prints report to stdout when omitted.",
    )
    monthly_parser.add_argument(
        "--goals",
        default=str(default_goals_path()),
        help="Path to goals yaml used for drift analysis.",
    )

    sync_report_parser = report_subparsers.add_parser(
        "sync", help="Generate sync status report."
    )
    sync_report_parser.add_argument(
        "--output",
        default=None,
        help="Optional output markdown file. Prints report to stdout when omitted.",
    )

    goals_report_parser = report_subparsers.add_parser(
        "goals", help="Generate goal allocation report."
    )
    goals_report_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Rolling window in days (default: 7).",
    )
    goals_report_parser.add_argument(
        "--output",
        default=None,
        help="Optional output markdown file. Prints report to stdout when omitted.",
    )
    goals_report_parser.add_argument(
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

    backup_parser = subparsers.add_parser(
        "backup",
        help="Create or restore a backup of the database and related config files.",
    )
    backup_parser.add_argument(
        "--config",
        default=_default_config_path_str(),
        help="Path to a simple YAML settings file.",
    )
    backup_subparsers = backup_parser.add_subparsers(dest="backup_command")
    backup_create_parser = backup_subparsers.add_parser(
        "create", help="Create a backup archive."
    )
    backup_create_parser.add_argument(
        "--output",
        default=None,
        help="Output archive path. Defaults to backups/workgraph-backup-<timestamp>.zip",
    )
    backup_create_parser.add_argument(
        "--include-config",
        action="store_true",
        default=True,
        help="Include config artifacts in the archive (default: true).",
    )
    backup_restore_parser = backup_subparsers.add_parser(
        "restore", help="Restore a backup archive or database."
    )
    backup_restore_parser.add_argument(
        "backup_path",
        help="Path to a backup archive (.zip) or database (.db).",
    )
    backup_restore_parser.add_argument(
        "--restore-config",
        action="store_true",
        default=True,
        help="Restore config artifacts from the archive when available (default: true).",
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Run database integrity checks and report common data issues.",
    )
    doctor_parser.add_argument(
        "--config",
        default=_default_config_path_str(),
        help="Path to a simple YAML settings file.",
    )

    sync_parser = subparsers.add_parser(
        "sync", help="Synchronize local data with central sync service."
    )
    sync_parser.add_argument(
        "--config",
        default=_default_config_path_str(),
        help="Path to a simple YAML settings file.",
    )
    sync_subparsers = sync_parser.add_subparsers(dest="sync_command")
    sync_once_parser = sync_subparsers.add_parser(
        "once", help="Run one push/pull sync cycle."
    )
    sync_once_parser.add_argument(
        "--config",
        default=_default_config_path_str(),
        help="Path to a simple YAML settings file.",
    )
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
    sync_catchup_parser = sync_subparsers.add_parser(
        "catchup",
        help="Run repeated sync cycles until local pending changes are drained.",
    )
    sync_catchup_parser.add_argument(
        "--config",
        default=_default_config_path_str(),
        help="Path to a simple YAML settings file.",
    )
    sync_catchup_parser.add_argument(
        "--base-url",
        default=None,
        help="Sync service base URL (fallback: sync_base_url in config).",
    )
    sync_catchup_parser.add_argument(
        "--token",
        default=None,
        help="Device token for sync API (fallback: sync_token in config).",
    )
    sync_catchup_parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Push batch size (fallback: sync_batch_size in config or default 1000).",
    )
    sync_catchup_parser.add_argument(
        "--pull-limit",
        type=int,
        default=None,
        help="Pull page size (fallback: sync_pull_limit in config or default 1000).",
    )
    sync_catchup_parser.add_argument(
        "--max-pull-pages",
        type=int,
        default=None,
        help="Max pull pages per cycle (fallback: sync_max_pull_pages in config or default 20).",
    )
    sync_catchup_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="HTTP timeout for sync requests (fallback: sync_timeout_seconds in config or default 10).",
    )
    sync_catchup_parser.add_argument(
        "--max-cycles",
        type=int,
        default=50,
        help="Maximum sync cycles to run before stopping (default: 50).",
    )
    sync_catchup_parser.add_argument(
        "--settle-cycles",
        type=int,
        default=2,
        help="Stop after this many consecutive no-progress cycles (default: 2).",
    )
    sync_daemon_parser = sync_subparsers.add_parser(
        "daemon", help="Run continuous sync loop with retry/backoff."
    )
    sync_daemon_parser.add_argument(
        "--config",
        default=_default_config_path_str(),
        help="Path to a simple YAML settings file.",
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
        help="Seconds between successful sync cycles (fallback: sync_interval_seconds in config or default 300).",
    )
    sync_daemon_parser.add_argument(
        "--backoff-base-seconds",
        type=float,
        default=None,
        help="Initial retry backoff seconds (fallback: sync_backoff_base_seconds in config or default 10).",
    )
    sync_daemon_parser.add_argument(
        "--backoff-max-seconds",
        type=float,
        default=None,
        help="Maximum retry backoff seconds (fallback: sync_backoff_max_seconds in config or default 300).",
    )
    sync_migrate_parser = sync_subparsers.add_parser(
        "migrate",
        help="Run local sync schema migration/backfill on the configured SQLite database.",
    )
    sync_migrate_parser.add_argument(
        "--config",
        default=_default_config_path_str(),
        help="Path to a simple YAML settings file.",
    )
    sync_validate_parser = sync_subparsers.add_parser(
        "validate",
        help="Validate sync data integrity in the local database.",
    )
    sync_validate_parser.add_argument(
        "--config",
        default=_default_config_path_str(),
        help="Path to a simple YAML settings file.",
    )
    sync_snapshot_parser = sync_subparsers.add_parser(
        "snapshot",
        help="Create a sync recovery snapshot archive.",
    )
    sync_snapshot_parser.add_argument(
        "--config",
        default=_default_config_path_str(),
        help="Path to a simple YAML settings file.",
    )
    sync_snapshot_parser.add_argument(
        "--output",
        default=None,
        help="Output archive path. Defaults to backups/sync-snapshot-<timestamp>.zip",
    )
    sync_snapshot_parser.add_argument(
        "--include-config",
        action="store_true",
        default=True,
        help="Include config artifacts in the snapshot (default: true).",
    )
    sync_verify_parser = sync_subparsers.add_parser(
        "verify",
        help="Compare local sync totals against the sync server.",
    )
    sync_verify_parser.add_argument(
        "--config",
        default=_default_config_path_str(),
        help="Path to a simple YAML settings file.",
    )
    sync_verify_parser.add_argument(
        "--base-url",
        default=None,
        help="Sync service base URL (fallback: sync_base_url in config).",
    )
    sync_verify_parser.add_argument(
        "--token",
        default=None,
        help="Device token for sync API (fallback: sync_token in config).",
    )
    sync_verify_parser.add_argument(
        "--pull-limit",
        type=int,
        default=None,
        help="Pull page size for verify (fallback: sync_pull_limit in config or default 1000).",
    )
    sync_verify_parser.add_argument(
        "--max-pages",
        type=int,
        default=100,
        help="Maximum pull pages used during verify (default: 100).",
    )
    sync_verify_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="HTTP timeout for verify calls (fallback: sync_timeout_seconds in config or default 10).",
    )
    args = parser.parse_args()

    if args.command == "export":
        run_export_command(args)
    elif args.command == "report" and args.report_command == "weekly":
        run_weekly_report_command(args)
    elif args.command == "report" and args.report_command == "monthly":
        run_monthly_report_command(args)
    elif args.command == "report" and args.report_command == "sync":
        run_sync_report_command(args)
    elif args.command == "report" and args.report_command == "goals":
        run_goals_report_command(args)
    elif args.command == "goals" and args.goals_command == "analyze":
        run_goals_analyze_command(args)
    elif args.command == "backup" and args.backup_command == "create":
        run_backup_create_command(args)
    elif args.command == "backup" and args.backup_command == "restore":
        run_backup_restore_command(args)
    elif args.command == "doctor":
        run_doctor_command(args)
    elif args.command == "sync" and args.sync_command == "once":
        run_sync_once_command(args)
    elif args.command == "sync" and args.sync_command == "daemon":
        run_sync_daemon_command(args)
    elif args.command == "sync" and args.sync_command == "catchup":
        run_sync_catchup_command(args)
    elif args.command == "sync" and args.sync_command == "migrate":
        run_sync_migrate_command(args)
    elif args.command == "sync" and args.sync_command == "snapshot":
        run_sync_snapshot_command(args)
    elif args.command == "sync" and args.sync_command == "validate":
        run_sync_validate_command(args)
    elif args.command == "sync" and args.sync_command == "verify":
        run_sync_verify_command(args)
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


def run_monthly_report_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    goals_path = Path(args.goals)
    goals_arg = str(goals_path) if goals_path.exists() else None

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            generate_monthly_report_markdown(
                db_path=settings.database_path,
                days=args.days,
                goals_path=goals_arg,
            ),
            encoding="utf-8",
        )
        print(f"Monthly report generated: {output_path}")
        return

    report = generate_monthly_report_markdown(
        db_path=settings.database_path,
        days=args.days,
        goals_path=goals_arg,
    )
    print(report)


def run_sync_report_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            generate_sync_report_markdown(db_path=settings.database_path),
            encoding="utf-8",
        )
        print(f"Sync report generated: {output_path}")
        return

    report = generate_sync_report_markdown(db_path=settings.database_path)
    print(report)


def run_goals_report_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    goals_path = Path(args.goals)
    goals_arg = str(goals_path) if goals_path.exists() else None

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            generate_goals_report_markdown(
                db_path=settings.database_path,
                days=args.days,
                goals_path=goals_arg,
            ),
            encoding="utf-8",
        )
        print(f"Goals report generated: {output_path}")
        return

    report = generate_goals_report_markdown(
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
        print(
            "Create config/my-goals.yaml or config/goals.yaml with a top-level 'goals' mapping."
        )
        return

    report = goal_drift_markdown(
        db_path=settings.database_path,
        goals_path=str(goals_path),
        days=args.days,
    )
    print(report)


def run_backup_create_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    db_path = Path(settings.database_path)
    if not db_path.exists():
        print("Backup create: FAIL")
        print(f"Database not found: {db_path}")
        return

    output_path = _backup_archive_path(args.output, prefix="workgraph-backup")
    archive_path = _create_backup_archive(
        db_path=db_path,
        output_path=output_path,
        include_config=bool(args.include_config),
        config_path=_resolve_settings_path(args.config),
        identity_path=Path(settings.identity_path),
    )

    print("Backup create: PASS")
    print(f"Archive: {archive_path}")
    print(f"Database: {db_path}")


def run_backup_restore_command(args: argparse.Namespace) -> None:
    backup_path = Path(args.backup_path)
    if not backup_path.exists():
        print("Backup restore: FAIL")
        print(f"Backup not found: {backup_path}")
        return

    try:
        if backup_path.suffix.lower() == ".zip" and bool(args.restore_config):
            _restore_backup_config_files(backup_path)

        settings = load_settings(args.config)
        db_path = Path(settings.database_path)
        restored = _restore_backup_database(
            backup_path=backup_path,
            db_path=db_path,
        )
    except Exception as exc:
        print("Backup restore: FAIL")
        print(str(exc))
        return

    print("Backup restore: PASS")
    print(f"Database: {db_path}")
    print(f"Restored from: {restored}")


def run_sync_snapshot_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    db_path = Path(settings.database_path)
    if not db_path.exists():
        print("Sync snapshot: FAIL")
        print(f"Database not found: {db_path}")
        return

    output_path = _backup_archive_path(args.output, prefix="sync-snapshot")
    archive_path = _create_backup_archive(
        db_path=db_path,
        output_path=output_path,
        include_config=bool(args.include_config),
        config_path=_resolve_settings_path(args.config),
        identity_path=Path(settings.identity_path),
    )

    print("Sync snapshot: PASS")
    print(f"Archive: {archive_path}")
    print(f"Database: {db_path}")


def _backup_archive_path(output: str | None, *, prefix: str) -> Path:
    if output:
        return Path(output)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("backups") / f"{prefix}-{stamp}.zip"


def _create_backup_archive(
    *,
    db_path: Path,
    output_path: Path,
    include_config: bool,
    config_path: Path,
    identity_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with ActivityRepository(db_path, identity_path=identity_path) as repository:
        db_backup_path = repository.backup_database(output_path.parent)

    archive_members: list[tuple[Path, str]] = [(db_backup_path, "activity.db")]
    if include_config:
        archive_members.extend(
            _backup_config_members(config_path=config_path, identity_path=identity_path)
        )

    with zipfile.ZipFile(
        output_path, mode="w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for source_path, archive_name in archive_members:
            if source_path.exists():
                archive.write(source_path, arcname=archive_name)

    try:
        db_backup_path.unlink(missing_ok=True)
    except OSError:
        pass

    return output_path


def _backup_config_members(
    *, config_path: Path, identity_path: Path
) -> list[tuple[Path, str]]:
    members: list[tuple[Path, str]] = []
    for source_path in [
        config_path,
        Path("config/my-settings.yaml"),
        Path("config/tags.yaml"),
        Path("config/goals.yaml"),
        Path("config/my-goals.yaml"),
        identity_path,
        Path("config/my-identity.json"),
    ]:
        if source_path.exists():
            members.append((source_path, _archive_member_name(source_path)))
    return members


def _archive_member_name(source_path: Path) -> str:
    resolved = source_path.resolve()
    cwd = Path.cwd().resolve()
    try:
        return resolved.relative_to(cwd).as_posix()
    except ValueError:
        return resolved.as_posix().lstrip("/")


def _restore_backup_artifact(
    *, backup_path: Path, db_path: Path, restore_config: bool
) -> Path:
    if backup_path.suffix.lower() == ".zip":
        if restore_config:
            _restore_backup_config_files(backup_path)
        return _restore_backup_database(backup_path=backup_path, db_path=db_path)

    if backup_path.suffix.lower() == ".db":
        shutil.copy2(backup_path, db_path)
        return backup_path

    raise ValueError("Backup must be a .zip archive or .db file")


def _restore_backup_config_files(backup_path: Path) -> None:
    with zipfile.ZipFile(backup_path, mode="r") as archive:
        for member in archive.namelist():
            if member == "activity.db":
                continue
            target_path = Path(member)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target_path.open("wb") as destination:
                shutil.copyfileobj(source, destination)


def _restore_backup_database(*, backup_path: Path, db_path: Path) -> Path:
    if backup_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(backup_path, mode="r") as archive:
            names = set(archive.namelist())
            if "activity.db" not in names:
                raise ValueError("Backup archive does not contain activity.db")

            db_path.parent.mkdir(parents=True, exist_ok=True)
            _remove_sqlite_sidecar_files(db_path)
            if db_path.exists():
                db_path.unlink()
            db_path.write_bytes(archive.read("activity.db"))
        return backup_path

    if backup_path.suffix.lower() == ".db":
        _remove_sqlite_sidecar_files(db_path)
        if db_path.exists():
            db_path.unlink()
        shutil.copy2(backup_path, db_path)
        return backup_path

    raise ValueError("Backup must be a .zip archive or .db file")


def _remove_sqlite_sidecar_files(db_path: Path) -> None:
    for suffix in ["-wal", "-shm", "-journal"]:
        sidecar = Path(f"{db_path}{suffix}")
        try:
            sidecar.unlink(missing_ok=True)
        except OSError:
            pass


def run_doctor_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    db_path = Path(settings.database_path)
    if not db_path.exists():
        print("Doctor: FAIL")
        print(f"Database not found: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    issues: list[str] = []
    try:
        integrity_issue = _run_sqlite_integrity_check(conn)
        if integrity_issue is not None:
            issues.append(integrity_issue)

        for table_name, indexes in _expected_index_map().items():
            missing = _missing_indexes(conn, table_name, indexes)
            for index_name in missing:
                issues.append(f"missing index: {table_name}.{index_name}")

        invalid_timestamp_issues = _collect_timestamp_issues(conn)
        issues.extend(invalid_timestamp_issues)

        rollup_issues = _collect_rollup_issues(conn)
        issues.extend(rollup_issues)
    finally:
        conn.close()

    if issues:
        print("Doctor: FAIL")
        for issue in issues:
            print(f"- {issue}")
        return

    print("Doctor: PASS")
    print(f"Database: {db_path}")
    print("Checked: integrity, indexes, timestamps, rollups")


def _run_sqlite_integrity_check(conn: sqlite3.Connection) -> str | None:
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    if not rows:
        return "sqlite integrity_check returned no rows"

    first_value = str(rows[0][0] if rows[0] else "")
    if first_value.lower() != "ok":
        details = "; ".join(str(row[0]) for row in rows if row and row[0])
        return f"sqlite integrity_check failed: {details or first_value}"
    return None


def _expected_index_map() -> dict[str, list[str]]:
    return {
        "activity_sessions": [
            "idx_activity_sessions_start_time",
            "idx_activity_sessions_app_name",
            "idx_activity_sessions_browser_domain",
            "idx_activity_sessions_device_updated",
            "idx_activity_sessions_user_updated",
        ],
        "journal_entries": [
            "idx_journal_entries_start_time",
            "idx_journal_entries_end_time",
            "idx_journal_entries_user_updated",
        ],
        "daily_reflections": [
            "idx_daily_reflections_date",
            "idx_daily_reflections_user_date",
        ],
        "work_events": [
            "idx_work_events_event_time",
            "idx_work_events_type",
            "idx_work_events_impact",
        ],
        "sync_metrics_daily": [
            "idx_sync_metrics_daily_user_day",
        ],
    }


def _missing_indexes(
    conn: sqlite3.Connection, table_name: str, index_names: list[str]
) -> list[str]:
    if not _table_exists(conn, table_name):
        return []

    existing_rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ?",
        (table_name,),
    ).fetchall()
    existing_names = {str(row[0]) for row in existing_rows}
    return [
        index_name for index_name in index_names if index_name not in existing_names
    ]


def _collect_timestamp_issues(conn: sqlite3.Connection) -> list[str]:
    issues: list[str] = []
    timestamp_checks = [
        (
            "activity_sessions",
            ["start_time", "end_time", "created_at", "updated_at"],
            False,
        ),
        (
            "journal_entries",
            ["created_at", "start_time", "end_time", "updated_at"],
            False,
        ),
        (
            "daily_reflections",
            ["date", "created_at", "updated_at"],
            True,
        ),
        (
            "work_events",
            ["created_at", "event_time"],
            False,
        ),
        (
            "sync_sessions",
            ["utc_start", "utc_end", "created_at", "updated_at"],
            False,
        ),
        (
            "sync_journal_entries",
            ["created_at", "updated_at"],
            False,
        ),
        (
            "sync_daily_reflections",
            ["date", "created_at", "updated_at"],
            True,
        ),
        (
            "sync_checkpoints",
            ["updated_at"],
            False,
        ),
        (
            "sync_request_logs",
            ["created_at"],
            False,
        ),
        (
            "sync_error_logs",
            ["created_at", "updated_at"],
            False,
        ),
        (
            "sync_metrics_daily",
            ["day_utc", "updated_at_utc"],
            True,
        ),
    ]

    for table_name, columns, treat_as_date in timestamp_checks:
        issues.extend(
            _timestamp_issues_for_table(
                conn,
                table_name=table_name,
                columns=columns,
                treat_date_columns=treat_as_date,
            )
        )
    return issues


def _timestamp_issues_for_table(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    columns: list[str],
    treat_date_columns: bool,
) -> list[str]:
    if not _table_exists(conn, table_name):
        return []

    available_columns = _table_columns(conn, table_name)
    issues: list[str] = []
    for column in columns:
        if column not in available_columns:
            continue
        invalid_count = _count_invalid_timestamp_values(
            conn, table_name, column, treat_date_columns
        )
        if invalid_count > 0:
            label = "date" if treat_date_columns and column == "date" else "timestamp"
            issues.append(
                f"{table_name}.{column} invalid {label} values: {invalid_count}"
            )
    return issues


def _count_invalid_timestamp_values(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    treat_as_date: bool,
) -> int:
    rows = conn.execute(
        f"SELECT {column_name} FROM {table_name} WHERE {column_name} IS NOT NULL AND TRIM({column_name}) != ''"
    ).fetchall()
    invalid = 0
    for row in rows:
        value = str(row[0]).strip()
        if treat_as_date:
            if _parse_date_str(value) is None:
                invalid += 1
        else:
            if _parse_datetime_str(value) is None:
                invalid += 1
    return invalid


def _collect_rollup_issues(conn: sqlite3.Connection) -> list[str]:
    if not _table_exists(conn, "sync_sessions") or not _table_exists(
        conn, "sync_metrics_daily"
    ):
        return []

    rows = conn.execute(
        """
        SELECT user_id, device_id, substr(utc_start, 1, 10) AS day_utc
        FROM sync_sessions
        WHERE deleted_at IS NULL
          AND utc_start IS NOT NULL
        GROUP BY user_id, device_id, day_utc
        ORDER BY user_id, device_id, day_utc
        """
    ).fetchall()

    issues: list[str] = []
    for row in rows:
        user_id = str(row["user_id"])
        device_id = str(row["device_id"])
        day_utc = str(row["day_utc"])
        if not user_id or not device_id or not day_utc:
            continue
        expected = _sync_rollup_expected_values(
            conn, user_id=user_id, device_id=device_id, day_utc=day_utc
        )
        stored = conn.execute(
            """
            SELECT active_seconds, focus_seconds, meeting_seconds, context_switches
            FROM sync_metrics_daily
            WHERE user_id = ? AND device_id = ? AND day_utc = ?
            """,
            (user_id, device_id, day_utc),
        ).fetchone()
        if stored is None:
            issues.append(f"sync rollup missing for {user_id}/{device_id}/{day_utc}")
            continue

        mismatched_fields: list[str] = []
        for field in [
            "active_seconds",
            "focus_seconds",
            "meeting_seconds",
            "context_switches",
        ]:
            if int(stored[field] or 0) != int(expected[field]):
                mismatched_fields.append(
                    f"{field}: stored={int(stored[field] or 0)} expected={int(expected[field])}"
                )
        if mismatched_fields:
            issues.append(
                f"sync rollup mismatch for {user_id}/{device_id}/{day_utc}: "
                + "; ".join(mismatched_fields)
            )

    return issues


def _sync_rollup_expected_values(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    device_id: str,
    day_utc: str,
) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS row_count,
            COALESCE(SUM(COALESCE(active_seconds, 0)), 0) AS active_seconds,
            COALESCE(SUM(COALESCE(CAST(json_extract(payload_json, '$.focus_seconds') AS INTEGER), 0)), 0) AS focus_seconds,
            COALESCE(
                SUM(
                    CASE
                        WHEN CAST(json_extract(payload_json, '$.meeting_seconds') AS INTEGER) IS NOT NULL
                            THEN COALESCE(CAST(json_extract(payload_json, '$.meeting_seconds') AS INTEGER), 0)
                        WHEN (
                            LOWER(COALESCE(application_name, CAST(json_extract(payload_json, '$.app_name') AS TEXT), '')) LIKE '%teams%'
                            OR LOWER(COALESCE(application_name, CAST(json_extract(payload_json, '$.app_name') AS TEXT), '')) LIKE '%zoom%'
                            OR LOWER(COALESCE(application_name, CAST(json_extract(payload_json, '$.app_name') AS TEXT), '')) LIKE '%webex%'
                            OR LOWER(COALESCE(application_name, CAST(json_extract(payload_json, '$.app_name') AS TEXT), '')) LIKE '%slack%'
                            OR LOWER(COALESCE(CAST(json_extract(payload_json, '$.browser_domain') AS TEXT), '')) LIKE '%meet.google.com%'
                            OR LOWER(COALESCE(CAST(json_extract(payload_json, '$.browser_domain') AS TEXT), '')) LIKE '%teams.microsoft.com%'
                            OR LOWER(COALESCE(CAST(json_extract(payload_json, '$.browser_domain') AS TEXT), '')) LIKE '%zoom.us%'
                            OR LOWER(COALESCE(CAST(json_extract(payload_json, '$.browser_domain') AS TEXT), '')) LIKE '%webex.com%'
                            OR LOWER(COALESCE(CAST(json_extract(payload_json, '$.window_title') AS TEXT), '')) LIKE '%meeting%'
                            OR LOWER(COALESCE(CAST(json_extract(payload_json, '$.window_title') AS TEXT), '')) LIKE '%standup%'
                            OR LOWER(COALESCE(CAST(json_extract(payload_json, '$.window_title') AS TEXT), '')) LIKE '%huddle%'
                        ) THEN COALESCE(active_seconds, 0)
                        ELSE 0
                    END
                ),
                0
            ) AS meeting_seconds,
            COALESCE(SUM(COALESCE(CAST(json_extract(payload_json, '$.context_switches') AS INTEGER), 0)), 0) AS context_switches_payload
        FROM sync_sessions
        WHERE user_id = ?
          AND device_id = ?
          AND deleted_at IS NULL
          AND utc_start IS NOT NULL
          AND substr(utc_start, 1, 10) = ?
        """,
        (user_id, device_id, day_utc),
    ).fetchone()

    if row is None or int(row["row_count"] or 0) == 0:
        return {
            "active_seconds": 0,
            "focus_seconds": 0,
            "meeting_seconds": 0,
            "context_switches": 0,
        }

    derived_switches_row = conn.execute(
        """
        WITH ordered AS (
            SELECT
                utc_start,
                COALESCE(application_name, CAST(json_extract(payload_json, '$.app_name') AS TEXT), '') AS app_name,
                COALESCE(CAST(json_extract(payload_json, '$.window_title') AS TEXT), '') AS window_title,
                COALESCE(CAST(json_extract(payload_json, '$.browser_domain') AS TEXT), '') AS browser_domain,
                LAG(COALESCE(application_name, CAST(json_extract(payload_json, '$.app_name') AS TEXT), ''))
                    OVER (ORDER BY utc_start, uuid) AS prev_app_name,
                LAG(COALESCE(CAST(json_extract(payload_json, '$.window_title') AS TEXT), ''))
                    OVER (ORDER BY utc_start, uuid) AS prev_window_title,
                LAG(COALESCE(CAST(json_extract(payload_json, '$.browser_domain') AS TEXT), ''))
                    OVER (ORDER BY utc_start, uuid) AS prev_browser_domain
            FROM sync_sessions
            WHERE user_id = ?
              AND device_id = ?
              AND deleted_at IS NULL
              AND utc_start IS NOT NULL
              AND substr(utc_start, 1, 10) = ?
        )
        SELECT COUNT(*) AS switch_count
        FROM ordered
        WHERE prev_app_name IS NOT NULL
          AND (
              COALESCE(app_name, '') != COALESCE(prev_app_name, '')
              OR COALESCE(window_title, '') != COALESCE(prev_window_title, '')
              OR COALESCE(browser_domain, '') != COALESCE(prev_browser_domain, '')
          )
        """,
        (user_id, device_id, day_utc),
    ).fetchone()

    derived_switches = int(
        (derived_switches_row["switch_count"] if derived_switches_row else 0) or 0
    )
    payload_switches = int(row["context_switches_payload"] or 0)

    return {
        "active_seconds": int(row["active_seconds"] or 0),
        "focus_seconds": int(row["focus_seconds"] or 0),
        "meeting_seconds": int(row["meeting_seconds"] or 0),
        "context_switches": max(payload_switches, derived_switches),
    }


def _parse_datetime_str(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed


def _parse_date_str(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def run_sync_once_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    resolved_config_path = _resolve_settings_path(args.config)
    raw_values = (
        _read_simple_yaml(resolved_config_path) if resolved_config_path.exists() else {}
    )

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
    max_pull_pages = int(
        args.max_pull_pages or raw_values.get("sync_max_pull_pages", 20)
    )
    timeout_seconds = float(
        args.timeout_seconds or raw_values.get("sync_timeout_seconds", 10.0)
    )

    worker_settings = SyncWorkerSettings(
        batch_size=batch_size,
        pull_limit=pull_limit,
        max_pull_pages=max_pull_pages,
    )
    client = HttpSyncClient(
        base_url=str(base_url), token=str(token), timeout_seconds=timeout_seconds
    )

    with ActivityRepository(
        settings.database_path, identity_path=settings.identity_path
    ) as repository:
        worker = SyncWorker(repository, client, worker_settings)
        try:
            summary = worker.run_once()
        except RuntimeError as exc:
            print("Sync failed")
            print(str(exc))
            return

    print("Sync complete")
    print(f"Push: {summary['push']}")
    print(f"Pull: {summary['pull']}")


def run_sync_daemon_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    resolved_config_path = _resolve_settings_path(args.config)
    raw_values = (
        _read_simple_yaml(resolved_config_path) if resolved_config_path.exists() else {}
    )

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
        max_pull_pages=int(
            args.max_pull_pages or raw_values.get("sync_max_pull_pages", 20)
        ),
    )
    daemon_settings = SyncDaemonSettings(
        interval_seconds=float(
            args.interval_seconds or raw_values.get("sync_interval_seconds", 300.0)
        ),
        backoff_base_seconds=float(
            args.backoff_base_seconds
            or raw_values.get("sync_backoff_base_seconds", 10.0)
        ),
        backoff_max_seconds=float(
            args.backoff_max_seconds
            or raw_values.get("sync_backoff_max_seconds", 300.0)
        ),
    )
    timeout_seconds = float(
        args.timeout_seconds or raw_values.get("sync_timeout_seconds", 10.0)
    )

    client = HttpSyncClient(
        base_url=str(base_url), token=str(token), timeout_seconds=timeout_seconds
    )

    print("Starting sync daemon. Press Ctrl+C to stop.")
    with ActivityRepository(
        settings.database_path, identity_path=settings.identity_path
    ) as repository:
        worker = SyncWorker(repository, client, worker_settings)
        daemon = SyncDaemon(worker, daemon_settings)
        daemon.run_forever()


def run_sync_catchup_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    resolved_config_path = _resolve_settings_path(args.config)
    raw_values = (
        _read_simple_yaml(resolved_config_path) if resolved_config_path.exists() else {}
    )

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
    max_pull_pages = int(
        args.max_pull_pages or raw_values.get("sync_max_pull_pages", 20)
    )
    timeout_seconds = float(
        args.timeout_seconds or raw_values.get("sync_timeout_seconds", 10.0)
    )
    max_cycles = max(1, int(args.max_cycles))
    settle_cycles = max(1, int(args.settle_cycles))

    worker_settings = SyncWorkerSettings(
        batch_size=batch_size,
        pull_limit=pull_limit,
        max_pull_pages=max_pull_pages,
    )
    client = HttpSyncClient(
        base_url=str(base_url), token=str(token), timeout_seconds=timeout_seconds
    )

    totals = {
        "push_sessions": 0,
        "push_journal_entries": 0,
        "push_daily_reflections": 0,
        "push_work_events": 0,
        "pull_sessions": 0,
        "pull_journal_entries": 0,
        "pull_daily_reflections": 0,
        "pull_work_events": 0,
        "pull_tombstones": 0,
    }

    no_progress_cycles = 0
    cycles_run = 0

    with ActivityRepository(
        settings.database_path, identity_path=settings.identity_path
    ) as repository:
        worker = SyncWorker(repository, client, worker_settings)

        for cycle in range(1, max_cycles + 1):
            summary = worker.run_once()
            cycles_run = cycle

            push = summary.get("push", {})
            pull = summary.get("pull", {})

            push_sessions = int(push.get("sessions", 0) or 0)
            push_journals = int(push.get("journal_entries", 0) or 0)
            push_reflections = int(push.get("daily_reflections", 0) or 0)
            push_work_events = int(push.get("work_events", 0) or 0)

            pull_sessions = int(pull.get("sessions", 0) or 0)
            pull_journals = int(pull.get("journal_entries", 0) or 0)
            pull_reflections = int(pull.get("daily_reflections", 0) or 0)
            pull_work_events = int(pull.get("work_events", 0) or 0)
            pull_tombstones = int(pull.get("tombstones", 0) or 0)

            totals["push_sessions"] += push_sessions
            totals["push_journal_entries"] += push_journals
            totals["push_daily_reflections"] += push_reflections
            totals["push_work_events"] += push_work_events
            totals["pull_sessions"] += pull_sessions
            totals["pull_journal_entries"] += pull_journals
            totals["pull_daily_reflections"] += pull_reflections
            totals["pull_work_events"] += pull_work_events
            totals["pull_tombstones"] += pull_tombstones

            cycle_progress = (
                push_sessions
                + push_journals
                + push_reflections
                + push_work_events
                + pull_sessions
                + pull_journals
                + pull_reflections
                + pull_work_events
                + pull_tombstones
            )

            print(
                f"cycle {cycle}: "
                f"push(s={push_sessions},j={push_journals},r={push_reflections},w={push_work_events}) "
                f"pull(s={pull_sessions},j={pull_journals},r={pull_reflections},w={pull_work_events},t={pull_tombstones})"
            )

            if cycle_progress == 0:
                no_progress_cycles += 1
                if no_progress_cycles >= settle_cycles:
                    break
            else:
                no_progress_cycles = 0

    print("Sync catchup complete")
    print(f"Cycles run: {cycles_run}")
    print(
        "Totals: "
        f"push(s={totals['push_sessions']},j={totals['push_journal_entries']},r={totals['push_daily_reflections']},w={totals['push_work_events']}), "
        f"pull(s={totals['pull_sessions']},j={totals['pull_journal_entries']},r={totals['pull_daily_reflections']},w={totals['pull_work_events']},t={totals['pull_tombstones']})"
    )


def run_sync_migrate_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    db_path = Path(settings.database_path)

    before = _count_sync_metadata_gaps(db_path)
    with ActivityRepository(
        settings.database_path, identity_path=settings.identity_path
    ):
        pass
    after = _count_sync_metadata_gaps(db_path)

    print("Sync migration complete")
    print(f"Database: {db_path}")
    print(f"Identity: {settings.identity_path}")
    print(
        f"Sessions with missing sync metadata: {before['activity_sessions']} -> {after['activity_sessions']}"
    )
    print(
        f"Journal entries with missing sync metadata: {before['journal_entries']} -> {after['journal_entries']}"
    )
    print(
        f"Reflections with missing sync metadata: {before['daily_reflections']} -> {after['daily_reflections']}"
    )


def run_sync_validate_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    db_path = Path(settings.database_path)
    if not db_path.exists():
        print("Sync validation: FAIL")
        print(f"Database not found: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    issues: list[str] = []
    try:
        session_missing = _count_missing_session_fields(conn)
        if session_missing > 0:
            issues.append(
                f"activity_sessions rows missing required fields: {session_missing}"
            )

        for table_name in [
            "activity_sessions",
            "journal_entries",
            "daily_reflections",
            "sync_sessions",
            "sync_journal_entries",
            "sync_daily_reflections",
        ]:
            empty_uuid_count, duplicate_uuid_count = _count_uuid_issues(
                conn, table_name
            )
            if empty_uuid_count > 0:
                issues.append(f"{table_name} empty UUID rows: {empty_uuid_count}")
            if duplicate_uuid_count > 0:
                issues.append(
                    f"{table_name} duplicate UUID groups: {duplicate_uuid_count}"
                )

        for table_name in [
            "sync_sessions",
            "sync_journal_entries",
            "sync_daily_reflections",
        ]:
            corrupt_payload_count = _count_corrupt_payload_rows(conn, table_name)
            if corrupt_payload_count > 0:
                issues.append(
                    f"{table_name} corrupt payload rows: {corrupt_payload_count}"
                )

        broken_git_refs = _count_broken_git_activity_refs(conn)
        if broken_git_refs > 0:
            issues.append(
                f"git_activity rows with missing session reference: {broken_git_refs}"
            )

        broken_sync_state_refs = _count_broken_sync_state_refs(conn)
        if broken_sync_state_refs > 0:
            issues.append(
                f"sync_state rows with missing device/user reference: {broken_sync_state_refs}"
            )
    finally:
        conn.close()

    if issues:
        print("Sync validation: FAIL")
        for item in issues:
            print(f"- {item}")
        return

    print("Sync validation: PASS")
    print(f"Database: {db_path}")
    print(
        "Checked: missing sessions, corrupt payloads, duplicate UUIDs, broken references"
    )


def run_sync_verify_command(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    db_path = Path(settings.database_path)
    if not db_path.exists():
        print("Sync verify: FAIL")
        print(f"Database not found: {db_path}")
        return

    resolved_config_path = _resolve_settings_path(args.config)
    raw_values = (
        _read_simple_yaml(resolved_config_path) if resolved_config_path.exists() else {}
    )

    base_url = args.base_url or raw_values.get("sync_base_url")
    token = args.token or raw_values.get("sync_token")
    if not base_url:
        print("Sync verify: FAIL")
        print("sync_base_url missing. Pass --base-url or set sync_base_url in config.")
        return
    if not token:
        print("Sync verify: FAIL")
        print("sync_token missing. Pass --token or set sync_token in config.")
        return

    pull_limit = int(args.pull_limit or raw_values.get("sync_pull_limit", 1000))
    max_pages = max(1, int(args.max_pages))
    timeout_seconds = float(
        args.timeout_seconds or raw_values.get("sync_timeout_seconds", 10.0)
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sync_state = _read_sync_state(conn)
        if sync_state is None:
            print("Sync verify: FAIL")
            print("sync_state missing. Run sync migrate/once first.")
            return
        local_totals = _local_sync_totals(conn)
    finally:
        conn.close()

    client = HttpSyncClient(
        base_url=str(base_url), token=str(token), timeout_seconds=timeout_seconds
    )
    try:
        server_totals, truncated = _pull_server_totals(
            client=client,
            device_id=sync_state["device_id"],
            user_id=sync_state["user_id"],
            pull_limit=pull_limit,
            max_pages=max_pages,
        )
    except RuntimeError as exc:
        print("Sync verify: FAIL")
        print(str(exc))
        return

    mismatches: list[str] = []
    for field in [
        "sessions",
        "journal_entries",
        "daily_reflections",
        "work_events",
        "active_seconds",
    ]:
        local_value = int(local_totals[field])
        server_value = int(server_totals[field])
        if local_value != server_value:
            mismatches.append(
                f"{field}: local={local_value} server={server_value} delta={local_value - server_value}"
            )

    if truncated:
        mismatches.append(
            f"server pagination truncated at {max_pages} pages; rerun with larger --max-pages"
        )

    if mismatches:
        print("Sync verify: FAIL")
        for item in mismatches:
            print(f"- {item}")
        return

    print("Sync verify: PASS")
    print(f"user_id: {sync_state['user_id']}")
    print(f"device_id: {sync_state['device_id']}")
    print(
        "Totals match: "
        f"sessions={local_totals['sessions']}, "
        f"journal_entries={local_totals['journal_entries']}, "
        f"daily_reflections={local_totals['daily_reflections']}, "
        f"work_events={local_totals['work_events']}, "
        f"active_seconds={local_totals['active_seconds']}"
    )


def _read_sync_state(conn: sqlite3.Connection) -> dict[str, str] | None:
    if not _table_exists(conn, "sync_state"):
        return None
    row = conn.execute(
        """
        SELECT device_id, user_id
        FROM sync_state
        WHERE id = 1
        """
    ).fetchone()
    if row is None:
        return None
    return {
        "device_id": str(row["device_id"] or ""),
        "user_id": str(row["user_id"] or ""),
    }


def _local_sync_totals(conn: sqlite3.Connection) -> dict[str, int]:
    sessions = _count_active_rows(conn, "activity_sessions")
    journals = _count_active_rows(conn, "journal_entries")
    reflections = _count_active_rows(conn, "daily_reflections")
    work_events = _count_active_rows(conn, "work_events")
    active_seconds = _sum_active_seconds(conn)
    return {
        "sessions": sessions,
        "journal_entries": journals,
        "daily_reflections": reflections,
        "work_events": work_events,
        "active_seconds": active_seconds,
    }


def _count_active_rows(conn: sqlite3.Connection, table_name: str) -> int:
    if not _table_exists(conn, table_name):
        return 0

    columns = _table_columns(conn, table_name)
    deleted_clause = ""
    if "deleted_at" in columns:
        deleted_clause = " WHERE deleted_at IS NULL"

    row = conn.execute(
        f"SELECT COUNT(*) AS total_rows FROM {table_name}{deleted_clause}"
    ).fetchone()
    return int(row["total_rows"] if row is not None else 0)


def _sum_active_seconds(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "activity_sessions"):
        return 0
    columns = _table_columns(conn, "activity_sessions")
    deleted_clause = ""
    if "deleted_at" in columns:
        deleted_clause = " AND deleted_at IS NULL"
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(duration_sec), 0) AS total_seconds
        FROM activity_sessions
        WHERE COALESCE(is_idle, 0) = 0{deleted_clause}
        """
    ).fetchone()
    return int(row["total_seconds"] if row is not None else 0)


def _pull_server_totals(
    *,
    client: HttpSyncClient,
    device_id: str,
    user_id: str,
    pull_limit: int,
    max_pages: int,
) -> tuple[dict[str, int], bool]:
    cursor: str | None = None
    totals = {
        "sessions": 0,
        "journal_entries": 0,
        "daily_reflections": 0,
        "work_events": 0,
        "active_seconds": 0,
    }
    truncated = False

    for page_index in range(max_pages):
        response = client.pull(
            {
                "device_id": device_id,
                "user_id": user_id,
                "cursor": cursor,
                "limit": pull_limit,
            }
        )

        changes = response.get("changes") or {}
        sessions = changes.get("sessions") or []
        journals = changes.get("journal_entries") or []
        reflections = changes.get("daily_reflections") or []
        work_events = changes.get("work_events") or []

        totals["sessions"] += len(sessions)
        totals["journal_entries"] += len(journals)
        totals["daily_reflections"] += len(reflections)
        totals["work_events"] += len(work_events)
        totals["active_seconds"] += sum(
            int(item.get("duration_sec") or 0) for item in sessions
        )

        cursor = response.get("next_cursor") or cursor
        has_more = bool(response.get("has_more"))
        if not has_more:
            break
        if page_index == max_pages - 1:
            truncated = True

    return totals, truncated


def _count_missing_session_fields(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "activity_sessions"):
        return 0
    row = conn.execute(
        """
        SELECT COUNT(*) AS missing_count
        FROM activity_sessions
        WHERE start_time IS NULL
           OR end_time IS NULL
           OR duration_sec IS NULL
           OR app_name IS NULL
           OR TRIM(app_name) = ''
        """
    ).fetchone()
    return int(row["missing_count"] if row is not None else 0)


def _count_uuid_issues(conn: sqlite3.Connection, table_name: str) -> tuple[int, int]:
    if not _table_exists(conn, table_name):
        return 0, 0
    columns = _table_columns(conn, table_name)
    if "uuid" not in columns:
        return 0, 0

    empty_row = conn.execute(
        f"""
        SELECT COUNT(*) AS empty_count
        FROM {table_name}
        WHERE uuid IS NULL OR TRIM(uuid) = ''
        """
    ).fetchone()
    empty_count = int(empty_row["empty_count"] if empty_row is not None else 0)

    if table_name.startswith("sync_") and "user_id" in columns:
        duplicate_row = conn.execute(
            f"""
            SELECT COUNT(*) AS duplicate_groups
            FROM (
                SELECT user_id, uuid
                FROM {table_name}
                WHERE uuid IS NOT NULL AND TRIM(uuid) != ''
                GROUP BY user_id, uuid
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()
    else:
        duplicate_row = conn.execute(
            f"""
            SELECT COUNT(*) AS duplicate_groups
            FROM (
                SELECT uuid
                FROM {table_name}
                WHERE uuid IS NOT NULL AND TRIM(uuid) != ''
                GROUP BY uuid
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()
    duplicate_groups = int(
        duplicate_row["duplicate_groups"] if duplicate_row is not None else 0
    )
    return empty_count, duplicate_groups


def _count_corrupt_payload_rows(conn: sqlite3.Connection, table_name: str) -> int:
    if not _table_exists(conn, table_name):
        return 0
    columns = _table_columns(conn, table_name)
    if "payload_json" not in columns:
        return 0

    rows = conn.execute(f"SELECT payload_json FROM {table_name}").fetchall()
    corrupt_count = 0
    for row in rows:
        payload_text = row["payload_json"]
        if payload_text is None or not str(payload_text).strip():
            corrupt_count += 1
            continue
        try:
            parsed = json.loads(str(payload_text))
        except json.JSONDecodeError:
            corrupt_count += 1
            continue
        if not isinstance(parsed, dict):
            corrupt_count += 1
    return corrupt_count


def _count_broken_git_activity_refs(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "git_activity") or not _table_exists(
        conn, "activity_sessions"
    ):
        return 0
    row = conn.execute(
        """
        SELECT COUNT(*) AS broken_count
        FROM git_activity g
        LEFT JOIN activity_sessions s ON s.id = g.session_id
        WHERE s.id IS NULL
        """
    ).fetchone()
    return int(row["broken_count"] if row is not None else 0)


def _count_broken_sync_state_refs(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "sync_state"):
        return 0
    if not _table_exists(conn, "devices") or not _table_exists(conn, "users"):
        return 0

    row = conn.execute(
        """
        SELECT COUNT(*) AS broken_count
        FROM sync_state ss
        LEFT JOIN devices d ON d.id = ss.device_id
        LEFT JOIN users u ON u.id = ss.user_id
        WHERE d.id IS NULL OR u.id IS NULL
        """
    ).fetchone()
    return int(row["broken_count"] if row is not None else 0)


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
    config_path = _resolve_settings_path(path)
    if config_path.exists():
        values.update(_read_simple_yaml(config_path))
    resolved_identity_path = _resolve_identity_path(str(values["identity_path"]))
    return CollectorSettings(
        database_path=str(values["database_path"]),
        poll_interval_seconds=float(values["poll_interval_seconds"]),
        idle_threshold_seconds=int(values["idle_threshold_seconds"]),
        session_gap_seconds=int(values["session_gap_seconds"]),
        browser_history_lookback_seconds=int(
            values["browser_history_lookback_seconds"]
        ),
        log_path=str(values["log_path"]),
        identity_path=resolved_identity_path,
    )


def _resolve_settings_path(path: str) -> Path:
    config_path = Path(path)
    if config_path == DEFAULT_SETTINGS_PATH and PERSONAL_SETTINGS_PATH.exists():
        return PERSONAL_SETTINGS_PATH
    return config_path


def _resolve_identity_path(path: str) -> str:
    identity_path = Path(path)
    if identity_path == DEFAULT_IDENTITY_PATH and PERSONAL_IDENTITY_PATH.exists():
        return str(PERSONAL_IDENTITY_PATH)
    return str(identity_path)


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
