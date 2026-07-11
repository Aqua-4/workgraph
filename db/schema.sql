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
    context_switches INTEGER NOT NULL DEFAULT 0,
    tag TEXT,
    platform TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS git_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    repo TEXT NOT NULL,
    branch TEXT,
    commit_hash TEXT,
    file_name TEXT,
    event_type TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES activity_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_activity_sessions_start_time
    ON activity_sessions (start_time);

CREATE INDEX IF NOT EXISTS idx_activity_sessions_app_name
    ON activity_sessions (app_name);

CREATE INDEX IF NOT EXISTS idx_activity_sessions_browser_domain
    ON activity_sessions (browser_domain);

CREATE INDEX IF NOT EXISTS idx_git_activity_session_id
    ON git_activity (session_id);

CREATE INDEX IF NOT EXISTS idx_git_activity_repo
    ON git_activity (repo);

CREATE TABLE IF NOT EXISTS journal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    title TEXT,
    notes TEXT,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_journal_entries_start_time
    ON journal_entries (start_time);

CREATE INDEX IF NOT EXISTS idx_journal_entries_end_time
    ON journal_entries (end_time);

CREATE TABLE IF NOT EXISTS daily_reflections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    wins TEXT,
    problems TEXT,
    tomorrow TEXT,
    energy INTEGER,
    stress INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_daily_reflections_date
    ON daily_reflections (date);
