PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS activity_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    duration_sec INTEGER NOT NULL,
    app_name TEXT NOT NULL,
    process_name TEXT,
    window_title TEXT,
    browser_domain TEXT,
    is_idle INTEGER NOT NULL,
    idle_seconds INTEGER NOT NULL DEFAULT 0,
    git_repo TEXT,
    git_branch TEXT,
    git_commit_hash TEXT,
    git_modified_files TEXT,
    context_switches INTEGER NOT NULL DEFAULT 0,
    tag TEXT,
    platform TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_activity_sessions_start_time
    ON activity_sessions (start_time);

CREATE INDEX IF NOT EXISTS idx_activity_sessions_app_name
    ON activity_sessions (app_name);

CREATE INDEX IF NOT EXISTS idx_activity_sessions_browser_domain
    ON activity_sessions (browser_domain);
