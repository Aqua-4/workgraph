from __future__ import annotations

from datetime import datetime
from pathlib import Path

from db.repository import ActivityRepository
from services.activity_tagger import ActivityTagger
from workgraph.models import ActivitySession


def retag_existing_sessions(
    database_path: str,
    tags_config: str | None = None,
    backup_dir: str = "backups",
) -> tuple[int, int, Path]:
    """Backup DB and recompute tags for all sessions.

    Returns (total_sessions, updated_sessions, backup_path).
    """
    tagger = ActivityTagger(tags_config)
    with ActivityRepository(database_path) as repository:
        backup_path = repository.backup_database(backup_dir)
        rows = repository.all_sessions()

        updated = 0
        for row in rows:
            session = _session_from_row(row)
            new_tag = tagger.tag_session(session)
            old_tag = row["tag"]
            if new_tag != old_tag:
                repository.update_session_tag(int(row["id"]), new_tag)
                updated += 1

    return len(rows), updated, backup_path


def _session_from_row(row) -> ActivitySession:
    start_time = datetime.fromisoformat(row["start_time"])
    end_time = datetime.fromisoformat(row["end_time"])
    return ActivitySession(
        start_time=start_time,
        end_time=end_time,
        duration_sec=int(row["duration_sec"]),
        app_name=row["app_name"],
        process_name=row["process_name"],
        window_title=row["window_title"],
        browser_domain=row["browser_domain"],
        is_idle=bool(row["is_idle"]),
        idle_seconds=int(row["idle_seconds"]),
        platform=row["platform"],
        git_repo=row["git_repo"],
        git_branch=row["git_branch"],
        context_switches=int(row["context_switches"]),
        tag=row["tag"],
    )
